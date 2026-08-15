import os
import shutil
import uuid

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Depends, Query

from app.core.config import get_settings
from app.core.errors import UnsupportedFileError, ValidationErrorApp
from app.db.base_repository import BaseRepository
from app.api.deps import get_repo
from app.schemas.schemas import ExtractionJobCreateResponse

router = APIRouter(tags=["extraction"])


@router.post("/extract", response_model=ExtractionJobCreateResponse, status_code=202)
async def extract(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="One or more P&ID PDF files"),
    project_id: str | None = Query(default=None, description="Existing project to attach this job to; a new project is created if omitted"),
    confidence_threshold: float = Query(default=0.75, ge=0.0, le=1.0),
    auto_learn_unknowns: bool = Query(default=False, description="If true, symbols matching a previously user-labeled shape signature are auto-resolved without a human"),
    repo: BaseRepository = Depends(get_repo),
):
    """
    Starts an extraction job for one or more uploaded P&ID PDFs.

    Processing runs asynchronously (BackgroundTasks by default, or a Celery
    worker if USE_CELERY=true) — poll GET /jobs/{job_id} for status and
    GET /jobs/{job_id}/result once complete. If any page contains a symbol
    below confidence_threshold, the job status becomes
    "paused_unknown_symbol" and GET /jobs/{job_id} will report pending
    unknowns; resolve them via POST /label-unknown-symbol to resume.
    """
    settings = get_settings()

    if not files:
        raise ValidationErrorApp("At least one PDF file is required.")

    if project_id:
        project = repo.get_project(project_id)
        if not project:
            raise ValidationErrorApp(f"project_id '{project_id}' does not exist.")
    else:
        project = repo.create_project(name=f"Untitled project {uuid.uuid4().hex[:8]}")
        project_id = project["id"]

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    saved_paths = []
    filenames = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            raise UnsupportedFileError(f"'{f.filename}' is not a PDF.")
        dest_path = os.path.join(settings.UPLOAD_DIR, f"{uuid.uuid4().hex[:8]}_{f.filename}")
        with open(dest_path, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved_paths.append(dest_path)
        filenames.append(f.filename)

    job = repo.create_extraction_job(
        project_id=project_id, source_filenames=filenames,
        confidence_threshold=confidence_threshold, auto_learn_unknowns=auto_learn_unknowns,
    )

    if settings.USE_CELERY:
        from app.celery_app import run_extraction_job_task
        run_extraction_job_task.delay(job["id"], saved_paths)
    else:
        from app.services.extraction.job_runner import run_extraction_job
        background_tasks.add_task(run_extraction_job, repo, job["id"], saved_paths)

    return ExtractionJobCreateResponse(
        job_id=job["id"], project_id=project_id, status=job["status"],
        pages_queued=0,  # actual page count known only after rendering, which happens async
        message="Extraction job created and queued for processing.",
    )
