"""
PDF -> multi-page VLM extraction.

Wraps app.services.extraction.pdf_renderer (PDF -> page PNGs) and
app.services.vlm.pid_expert_analyst (single-page-image -> four tables +
unknown_symbols) into one call: render every page of an uploaded P&ID PDF,
run the expert-analyst pass on each, and merge into a single
PidExtractionResult whose page_number fields already say which page each
row came from.

Also cuts an actual crop image (PNG) for every unknown_symbols entry from
the rendered page it came from — the VLM only ever reports a bbox +
description, never image bytes, so that has to happen here rather than in
the model call itself. Crops are saved under settings.CROP_DIR and their
paths are returned alongside the result so the caller (the DB-save step)
can persist them for the teaching UI.
"""
import os
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.schemas.vlm_schemas import PidExtractionResult
from app.services.extraction.pdf_renderer import render_pdf_to_images
from app.services.vlm.pid_expert_analyst import extract_pid_tables

logger = get_logger(__name__)


@dataclass
class PdfExtractionOutcome:
    result: PidExtractionResult
    page_count: int
    model_used: str | None = None
    unknown_symbol_crop_paths: list[str | None] = field(default_factory=list)  # parallel to result.unknown_symbols
    notes: list[str] = field(default_factory=list)


def _crop_unknown_symbol_image(page_image_path: str, bbox: list[float], out_dir: str, tag: str) -> str | None:
    """Best-effort crop of a rendered page around an unknown-symbol bbox. Returns None (never raises) on any failure — a missing crop just means the teaching UI shows no thumbnail for that entry."""
    if not bbox or len(bbox) != 4 or bbox == [0.0, 0.0, 0.0, 0.0]:
        return None
    try:
        from PIL import Image
        os.makedirs(out_dir, exist_ok=True)
        with Image.open(page_image_path) as img:
            x1, y1, x2, y2 = bbox
            # Small padding so the crop isn't razor-tight around the symbol.
            pad = 8
            x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
            x2, y2 = min(img.width, x2 + pad), min(img.height, y2 + pad)
            if x2 <= x1 or y2 <= y1:
                return None
            crop = img.crop((x1, y1, x2, y2))
            out_path = os.path.join(out_dir, f"unknown_{tag}.png")
            crop.convert("RGB").save(out_path, format="PNG")
            return out_path
    except Exception as exc:
        logger.warning("unknown_symbol_crop_failed", extra={"context": {"error": str(exc)}})
        return None


def analyze_pdf(pdf_path: str, model: str | None = None) -> PdfExtractionOutcome:
    """
    Renders every page of pdf_path and runs the expert-analyst extraction
    on each, merging results in page order. Never raises for a single bad
    page — that page's extraction_notes (if any) is collected into
    outcome.notes and its four tables/unknowns just come back empty,
    matching extract_pid_tables' own never-fail-hard behavior.
    """
    settings = get_settings()
    render_dir = os.path.join(settings.STORAGE_DIR, "vlm_pages")
    pages = render_pdf_to_images(pdf_path, render_dir, dpi=settings.RENDER_DPI)

    merged = PidExtractionResult()
    crop_paths: list[str | None] = []
    notes: list[str] = []
    used_model = None
    import uuid as _uuid

    for page in pages:
        page_result = extract_pid_tables(page.image_path, model=model, page_number=page.page_number)
        used_model = used_model or page_result.model_used
        if page_result.extraction_notes:
            notes.append(f"page {page.page_number}: {page_result.extraction_notes}")

        merged.instruments.extend(page_result.instruments)
        merged.equipment.extend(page_result.equipment)
        merged.pipe_runs.extend(page_result.pipe_runs)
        merged.piping_components.extend(page_result.piping_components)

        crop_dir = os.path.join(settings.CROP_DIR, "vlm_unknown")
        for unk in page_result.unknown_symbols:
            merged.unknown_symbols.append(unk)
            crop_paths.append(_crop_unknown_symbol_image(page.image_path, unk.bbox, crop_dir, _uuid.uuid4().hex[:10]))

    logger.info("pdf_analyzed", extra={"context": {
        "pdf_path": pdf_path, "pages": len(pages),
        "instruments": len(merged.instruments), "equipment": len(merged.equipment),
        "pipe_runs": len(merged.pipe_runs), "piping_components": len(merged.piping_components),
        "unknown_symbols": len(merged.unknown_symbols),
    }})

    return PdfExtractionOutcome(
        result=merged, page_count=len(pages), model_used=used_model,
        unknown_symbol_crop_paths=crop_paths, notes=notes,
    )
