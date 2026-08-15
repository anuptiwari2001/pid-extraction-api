"""
Standalone "expert P&ID analyst" endpoints.

This is deliberately separate from the /extract job pipeline (CV symbol
detection + OCR + rule-based/GNN relationship inference): it's a single
vision-language-model pass over one page image — or a specific region of
one — that extracts structured engineering data straight into four tables
(instruments, equipment, pipe_runs, piping_components), classified against
ISA-5.1 / PIP. Runs against Ollama locally by default — no API key needed.
"""
import json
import os
import shutil
import uuid

from fastapi import APIRouter, UploadFile, File, Form, Query, Depends

from app.core.config import get_settings
from app.core.errors import UnsupportedFileError, NotFoundError, ValidationErrorApp
from app.core.logging_config import get_logger
from app.db.base_repository import BaseRepository
from app.api.deps import get_repo
from app.schemas.vlm_schemas import PidExtractionResult
from app.schemas.teaching_schemas import (
    TeachingSuggestionResult, TeachSymbolResponse, TeachingCategory, RecommendedTable,
)
from app.services.vlm.pid_expert_analyst import extract_pid_tables
from app.services.vlm.teaching import suggest_teaching

router = APIRouter(prefix="/vlm", tags=["vlm-expert-analyst"])
logger = get_logger(__name__)

_ALLOWED_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")


def _region_tuple(x1: float | None, y1: float | None, x2: float | None, y2: float | None):
    """Returns a (x1,y1,x2,y2) tuple only if all four region bounds were given; else None (= full image)."""
    coords = (x1, y1, x2, y2)
    if all(c is None for c in coords):
        return None
    if any(c is None for c in coords):
        raise ValidationErrorApp("To analyze a region, provide all four of x1, y1, x2, y2 (or none, for the full image).")
    if x2 <= x1 or y2 <= y1:
        raise ValidationErrorApp("Region bbox must have x2 > x1 and y2 > y1.")
    return (x1, y1, x2, y2)


def _counts(result: PidExtractionResult) -> dict:
    return {
        "instruments": len(result.instruments), "equipment": len(result.equipment),
        "pipe_runs": len(result.pipe_runs), "piping_components": len(result.piping_components),
    }


@router.post("/analyze", response_model=PidExtractionResult)
async def analyze_uploaded_image(
    file: UploadFile = File(..., description="A single P&ID page as an image (PNG/JPEG). For a PDF, render pages first via POST /extract, then use GET /vlm/analyze-page/{job_id}/{page_number}."),
    model: str | None = Query(default=None, description="Override the configured VLM model for this call only"),
    page_number: int = Query(default=1, ge=1, description="Recorded on every extracted row's page_number field"),
    x1: float | None = Query(default=None, description="Region bbox left, in source-image pixels — omit all four x1/y1/x2/y2 to analyze the full page"),
    y1: float | None = Query(default=None, description="Region bbox top"),
    x2: float | None = Query(default=None, description="Region bbox right"),
    y2: float | None = Query(default=None, description="Region bbox bottom"),
):
    """
    Runs the expert P&ID analyst over a single uploaded image — the full
    page, or just the region given by x1/y1/x2/y2 — and returns four
    tables: instruments, equipment, pipe_runs, piping_components, each row
    populated per ISA-5.1 (instruments) / PIP (equipment, pipe runs, piping
    components). Fields the model can't clearly read are left null/empty
    rather than guessed. bbox values (instruments/equipment/piping_
    components; pipe_runs have none) are always in the original full-image
    pixel space, even for a cropped region.

    Uses VLM_PROVIDER from settings (default: "ollama", a local server —
    no API key involved). Never fails hard on a bad/unreachable model: if
    extraction can't complete, the response comes back with all four
    tables empty and an explanation in extraction_notes instead of a 500.
    """
    if not file.filename.lower().endswith(_ALLOWED_IMAGE_EXT):
        raise UnsupportedFileError(
            f"'{file.filename}' is not a supported image type ({', '.join(_ALLOWED_IMAGE_EXT)}). "
            f"For PDFs, submit via POST /extract instead so pages are rendered first."
        )

    region = _region_tuple(x1, y1, x2, y2)

    settings = get_settings()
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    tmp_path = os.path.join(settings.UPLOAD_DIR, f"vlm_{uuid.uuid4().hex[:8]}_{file.filename}")
    with open(tmp_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        result = extract_pid_tables(tmp_path, region=region, model=model, page_number=page_number)
    finally:
        # This is a scratch copy purely for the VLM call, not a pipeline
        # artifact — nothing downstream references it, so clean it up.
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    logger.info("vlm_expert_extraction_complete", extra={"context": {
        "filename": file.filename, "region": region, **_counts(result),
    }})
    return result


@router.get("/analyze-page/{job_id}/{page_number}", response_model=PidExtractionResult)
def analyze_pipeline_page(
    job_id: str,
    page_number: int,
    model: str | None = Query(default=None, description="Override the configured VLM model for this call only"),
    x1: float | None = Query(default=None, description="Region bbox left, in page pixels — omit all four x1/y1/x2/y2 to analyze the full page"),
    y1: float | None = Query(default=None),
    x2: float | None = Query(default=None),
    y2: float | None = Query(default=None),
    repo: BaseRepository = Depends(get_repo),
):
    """
    Runs the same expert-analyst extraction against a page image that was
    already rendered by an earlier POST /extract job — the full page, or
    just a region of it — useful as an independent cross-check against
    that job's CV/OCR-derived result for the same page
    (GET /jobs/{job_id}/result), without re-uploading the source PDF.
    """
    pages = repo.get_pages_for_job(job_id)
    if not pages:
        raise NotFoundError(f"Job {job_id} not found or has no rendered pages yet")

    page = next((p for p in pages if p["page_number"] == page_number), None)
    if not page:
        raise NotFoundError(f"Page {page_number} not found for job {job_id}")

    image_path = page.get("image_path")
    if not image_path or not os.path.exists(image_path):
        raise ValidationErrorApp(f"Rendered image for page {page_number} is not available on disk.")

    region = _region_tuple(x1, y1, x2, y2)
    result = extract_pid_tables(image_path, region=region, model=model, page_number=page_number)
    logger.info("vlm_expert_extraction_complete", extra={"context": {
        "job_id": job_id, "page_number": page_number, "region": region, **_counts(result),
    }})
    return result


@router.post("/suggest-teaching", response_model=TeachingSuggestionResult)
async def suggest_teaching_for_symbol(
    file: UploadFile = File(..., description="Crop image of the unrecognized symbol"),
    nearby_text: str = Form(default="", description="Text found near the symbol on the drawing, if any"),
    proposed_class: str = Form(default="", description="Current best-guess class, if any"),
    confidence: float = Form(default=0.0, ge=0.0, le=1.0, description="Confidence of the current best-guess class"),
    model: str | None = Query(default=None, description="Override the configured VLM model for this call only"),
):
    """
    Unknown/non-standard symbol teaching — AI-assisted suggestion step.

    Sends the symbol crop plus context to the VLM and asks it to propose a
    classification (category, name, ISA-5.1/PIP equivalents, target table)
    in the exact "teach" JSON contract. This is ONLY a suggestion meant to
    pre-fill the Human-in-the-Loop popup — nothing is written to
    symbol_dictionary here. The human reviews/edits the fields (Category,
    Name/Type, Tag format, Description, Table) and submits the final
    version to POST /vlm/teach-symbol, which does the actual save.

    Never fails hard: if the VLM can't produce a usable suggestion,
    `suggestion` comes back null with `notes` explaining why, so the
    frontend can fall back to an empty popup instead of erroring out.
    """
    settings = get_settings()
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    tmp_path = os.path.join(settings.UPLOAD_DIR, f"teach_{uuid.uuid4().hex[:8]}_{file.filename}")
    with open(tmp_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        result = suggest_teaching(tmp_path, nearby_text=nearby_text, proposed_class=proposed_class, confidence=confidence, model=model)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    logger.info("teaching_suggestion_complete", extra={"context": {
        "filename": file.filename, "has_suggestion": result.suggestion is not None,
    }})
    return result


@router.post("/teach-symbol", response_model=TeachSymbolResponse)
async def teach_symbol(
    file: UploadFile = File(..., description="Crop image of the symbol being taught"),
    category: TeachingCategory = Form(...),
    name_type: str = Form(..., description="Name / Type — e.g. 'Special Ball Valve - Fire Safe'"),
    recommended_table: RecommendedTable = Form(..., description="Which table this data should go into"),
    description: str = Form(...),
    tag_format: str | None = Form(default=None, description="Tag format, if any — e.g. 'BV-XXXX'"),
    standard_reference: str = Form(default="None", description="None / Custom / Company Standard"),
    isa_equivalent: str | None = Form(default=None),
    pip_equivalent: str | None = Form(default=None),
    default_attributes: str = Form(default="{}", description="JSON object string of default attributes for future recognitions of this symbol"),
    repo: BaseRepository = Depends(get_repo),
):
    """
    Unknown/non-standard symbol teaching — final human-confirmed save step.

    Matches the Human-in-the-Loop popup exactly: Category, Name/Type, Tag
    format, Description, and which table the data belongs in (plus the
    optional ISA-5.1/PIP-equivalent and standard-reference fields carried
    over from an AI suggestion, if the human confirmed one via
    POST /vlm/suggest-teaching — otherwise leave those blank).

    On success:
    1. Adds the symbol to symbol_dictionary (category_name=name_type,
       tagged with its target table, ISA/PIP equivalents, and any default
       attributes for future extractions).
    2. Saves the example crop under CROP_DIR.
    3. Computes a shape signature from the crop so a visually similar
       symbol can be auto-resolved next time, without asking a human twice
       for the same shape (same mechanism as the existing
       POST /label-unknown-symbol flow).
    """
    try:
        attrs = json.loads(default_attributes) if default_attributes else {}
        if not isinstance(attrs, dict):
            raise ValueError("default_attributes must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValidationErrorApp(f"default_attributes must be a valid JSON object: {exc}")

    settings = get_settings()
    os.makedirs(settings.CROP_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename)[1] or ".png"
    crop_path = os.path.join(settings.CROP_DIR, f"taught_{uuid.uuid4().hex[:8]}{ext}")
    with open(crop_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    shape_signature = None
    try:
        import cv2
        from app.services.cv.symbol_signature import compute_signature
        crop = cv2.imread(crop_path, cv2.IMREAD_GRAYSCALE)
        if crop is not None:
            shape_signature = compute_signature(crop)
    except Exception:
        pass  # best-effort — a missing signature just skips auto-resolution next time, doesn't block teaching

    merged_attributes = {
        "recommended_table": recommended_table,
        "standard_reference": standard_reference,
        "tag_format": tag_format,
        "pip_equivalent": pip_equivalent,
        **attrs,
    }

    entry = repo.add_symbol_dictionary_entry(
        category_name=name_type,
        source="vlm_taught",
        isa_type_code=isa_equivalent,
        description=description,
        reference_crop_path=crop_path,
        shape_signature=shape_signature,
        attributes_schema=merged_attributes,
    )

    logger.info("symbol_taught", extra={"context": {
        "category_name": name_type, "category": category, "recommended_table": recommended_table,
    }})

    return TeachSymbolResponse(
        symbol_dictionary_id=entry.get("id") or entry.get("_id"),
        category_name=name_type,
        recommended_table=recommended_table,
        added_to_dictionary=True,
        crop_saved_path=crop_path,
    )

