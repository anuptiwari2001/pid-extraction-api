"""
Drives an extraction job end-to-end across all its pages. Called either
directly by a FastAPI BackgroundTask (default, simplest to run/demo) or by
a Celery task (see app/celery_app.py) when USE_CELERY=true — same function,
different caller, so behavior is identical either way.
"""
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.db.base_repository import BaseRepository
from app.services.extraction.orchestrator import render_and_register_pages, process_page

logger = get_logger(__name__)


def run_extraction_job(repo: BaseRepository, job_id: str, pdf_paths: list[str]) -> None:
    settings = get_settings()
    job = repo.get_job(job_id)
    if not job:
        logger.error("job_not_found", extra={"context": {"job_id": job_id}})
        return

    try:
        repo.update_job_status(job_id, "running", progress_pct=0.0)
        pages = render_and_register_pages(repo, job, pdf_paths, settings)

        if not pages:
            repo.update_job_status(job_id, "failed", error_message="No pages rendered from uploaded PDF(s).")
            return

        completed, paused = 0, 0
        for i, page in enumerate(pages, start=1):
            result = process_page(
                repo, job, page,
                confidence_threshold=job["confidence_threshold"],
                auto_learn_unknowns=job["auto_learn_unknowns"],
            )
            if result["status"] == "paused_unknown_symbol":
                paused += 1
            else:
                completed += 1
            repo.update_job_status(job_id, "running", progress_pct=round(100 * i / len(pages), 1))

        final_status = "paused_unknown_symbol" if paused > 0 else "completed"
        repo.update_job_status(job_id, final_status, progress_pct=100.0)
        logger.info("job_finished", extra={"context": {"job_id": job_id, "completed_pages": completed, "paused_pages": paused}})

    except Exception as exc:
        logger.exception("job_failed", extra={"context": {"job_id": job_id}})
        repo.update_job_status(job_id, "failed", error_message=str(exc))


def resume_job_if_unblocked(repo: BaseRepository, job_id: str) -> None:
    """
    Called after a symbol is labeled. If no unknowns remain pending for the
    job, re-runs structuring (stage 5) for any page that was paused, then
    flips the job back to running/completed as appropriate.
    """
    pending = repo.get_pending_unknown_symbols(job_id)
    if pending:
        return  # still blocked on other unknowns

    job = repo.get_job(job_id)
    pages = repo.get_pages_for_job(job_id)
    paused_pages = [p for p in pages if p["status"] == "paused"]

    from app.services.extraction.orchestrator import _finish_page_structuring
    from app.services.ocr.text_extractor import extract_text_for_page

    for page in paused_pages:
        # Symbols (including the now-resolved ones) were already saved during
        # the initial pass; pull them back for structuring rather than
        # re-running detection.
        saved_symbols = repo.get_symbols_for_page(page["id"])
        text_regions = extract_text_for_page(page["image_path"])
        _finish_page_structuring(repo, job, page, saved_symbols, text_regions)
        repo.update_page_status(page["id"], "completed")

    repo.update_job_status(job_id, "completed", progress_pct=100.0)
    logger.info("job_resumed_and_completed", extra={"context": {"job_id": job_id}})
