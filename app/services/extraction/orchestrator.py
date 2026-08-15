"""
The extraction orchestrator. This is the one place that knows the full
pipeline order (matches the spec exactly):

  1. Analyze P&ID images        -> pdf_renderer.render_pdf_to_images
  2. Recognize symbols (CV)     -> cv.symbol_detector
  3. Extract text (OCR/VLM)     -> ocr.text_extractor
  4. Infer relationships (GNN)  -> gnn.rule_based_relations (+ gnn.gnn_model)
  5. Build structured twin      -> this module, classification.py
  6. Persist                    -> db.base_repository (whichever backend is active)

Unknown-symbol handling: when a detection is below confidence_threshold (or
literally class "unknown"), the page is PAUSED — its remaining symbols are
still detected and saved, but relationship inference and downstream
structuring for that page wait until every unknown on it is resolved via
POST /label-unknown-symbol (or, if auto_learn_unknowns=True and a stored
shape signature matches a prior label, it's auto-resolved without a human).
The job as a whole keeps processing other pages while one is paused.
"""
import os
from dataclasses import dataclass
from time import time

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.db.base_repository import BaseRepository
from app.services.extraction.pdf_renderer import render_pdf_to_images
from app.services.cv.symbol_detector import get_symbol_detector, Detection
from app.services.cv.symbol_signature import compute_signature
from app.services.ocr.text_extractor import extract_text_for_page, parse_isa_tag
from app.services.extraction.classification import classify_symbol
from app.services.extraction.line_tracer import trace_lines, snap_endpoints_to_entities
from app.services.gnn.rule_based_relations import infer_relationships, Entity
from app.services.gnn.gnn_model import maybe_refine_with_gnn

logger = get_logger(__name__)


def _nearest_text(bbox, text_regions, max_distance: float = 80.0):
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    best, best_d = None, float("inf")
    for region in text_regions:
        rx1, ry1, rx2, ry2 = region.bbox
        rcx, rcy = (rx1 + rx2) / 2, (ry1 + ry2) / 2
        d = ((cx - rcx) ** 2 + (cy - rcy) ** 2) ** 0.5
        if d < best_d:
            best, best_d = region, d
    if best and best_d <= max_distance:
        return best
    return None


def process_page(
    repo: BaseRepository,
    job: dict,
    page_row: dict,
    confidence_threshold: float,
    auto_learn_unknowns: bool,
) -> dict:
    """
    Runs stages 2-5 for a single already-rendered page. Returns a dict with
    keys: status ("completed"|"paused_unknown_symbol"), and, if completed,
    the counts of entities created.

    Includes detailed timing logs to identify which stage is slow/timing out.
    """
    image_path = page_row["image_path"]
    page_id = page_row["id"]
    timer_start = time()

    # Stage 2: Detect symbols (CV)
    stage_timer = time()
    detector = get_symbol_detector()
    detections: list[Detection] = detector.detect(image_path)
    detect_time = time() - stage_timer
    logger.debug(
        "extraction_stage_timing",
        extra={
            "context": {
                "page_id": page_id,
                "stage": "symbol_detection",
                "duration_seconds": round(detect_time, 2),
                "symbol_count": len(detections),
            }
        },
    )

    # Stage 3: Extract text (OCR/VLM) — can be slow on CPU
    stage_timer = time()
    text_regions = extract_text_for_page(image_path)
    text_time = time() - stage_timer
    logger.debug(
        "extraction_stage_timing",
        extra={
            "context": {
                "page_id": page_id,
                "stage": "text_extraction",
                "duration_seconds": round(text_time, 2),
                "text_region_count": len(text_regions),
            }
        },
    )

    # Pull the pre-existing user-labeled dictionary once per page so repeated
    # signature lookups don't hit the DB per-symbol.
    known_dictionary = {d["category_name"]: d for d in repo.get_symbol_dictionary()}

    symbol_rows = []
    unknown_created = []
    for det in detections:
        nearby = _nearest_text(det.bbox, text_regions)
        nearby_text = nearby.text if nearby else None

        is_unknown = det.class_name == "unknown" or det.confidence < confidence_threshold
        resolved_class = det.class_name

        if is_unknown and det.shape_signature:
            match = repo.find_symbol_dictionary_by_signature(det.shape_signature)
            if match:
                resolved_class = match["category_name"]
                is_unknown = False
                logger.info("unknown_auto_resolved_by_signature", extra={"context": {"category": resolved_class}})

        symbol_rows.append({
            "class_name": resolved_class,
            "confidence": det.confidence,
            "bbox": {"x1": det.bbox[0], "y1": det.bbox[1], "x2": det.bbox[2], "y2": det.bbox[3]},
            "extracted_text": nearby_text,
            "is_unknown": is_unknown,
        })

    # Stage 4a: Save raw symbols to DB
    stage_timer = time()
    saved_symbols = repo.save_symbols(page_id, symbol_rows)
    save_symbols_time = time() - stage_timer
    logger.debug(
        "extraction_stage_timing",
        extra={
            "context": {
                "page_id": page_id,
                "stage": "save_symbols",
                "duration_seconds": round(save_symbols_time, 2),
            }
        },
    )

    # Re-pair saved (now with real IDs) symbols against their unknown status
    for saved, original in zip(saved_symbols, detections):
        if saved.get("is_unknown"):
            unknown_created.append((saved, original))

    if unknown_created:
        settings = get_settings()
        for saved, original in unknown_created:
            crop_path = _save_crop(image_path, saved["bbox_x1"], saved["bbox_y1"], saved["bbox_x2"], saved["bbox_y2"], settings.CROP_DIR)
            nearby = _nearest_text(
                (saved["bbox_x1"], saved["bbox_y1"], saved["bbox_x2"], saved["bbox_y2"]), text_regions
            )
            unk = repo.save_unknown_symbol(
                job_id=job["id"], page_id=page_id, page_number=page_row["page_number"],
                symbol_id=saved["id"],
                bbox={"x1": saved["bbox_x1"], "y1": saved["bbox_y1"], "x2": saved["bbox_x2"], "y2": saved["bbox_y2"]},
                crop_image_path=crop_path,
                surrounding_text=(nearby.text if nearby else None),
                original_confidence=saved["confidence"],
            )

            if auto_learn_unknowns:
                # Nothing to auto-label yet (no human input) — this flag
                # only affects whether we require the human step before
                # RESUMING; it does not fabricate a label. Documented in
                # the /extract endpoint docstring.
                pass

        repo.update_page_status(page_id, "paused")
        logger.info(
            "page_processing_paused_unknown",
            extra={
                "context": {
                    "page_id": page_id,
                    "unknown_count": len(unknown_created),
                    "total_time_seconds": round(time() - timer_start, 2),
                }
            },
        )
        return {"status": "paused_unknown_symbol", "pending_unknowns": len(unknown_created)}

    # Stage 5: Classify and infer relationships
    stage_timer = time()
    _finish_page_structuring(repo, job, page_row, saved_symbols, text_regions)
    structuring_time = time() - stage_timer
    logger.debug(
        "extraction_stage_timing",
        extra={
            "context": {
                "page_id": page_id,
                "stage": "page_structuring",
                "duration_seconds": round(structuring_time, 2),
            }
        },
    )

    repo.update_page_status(page_id, "completed")
    total_time = time() - timer_start
    logger.info(
        "page_processing_completed",
        extra={
            "context": {
                "page_id": page_id,
                "total_time_seconds": round(total_time, 2),
                "symbol_count": len(saved_symbols),
            }
        },
    )
    return {"status": "completed"}


def _save_crop(image_path: str, x1: float, y1: float, x2: float, y2: float, crop_dir: str) -> str:
    from PIL import Image
    os.makedirs(crop_dir, exist_ok=True)
    with Image.open(image_path) as img:
        crop = img.crop((x1, y1, x2, y2))
        crop_path = os.path.join(crop_dir, f"crop_{os.path.basename(image_path)}_{int(x1)}_{int(y1)}.png")
        crop.save(crop_path)
        return crop_path


def _finish_page_structuring(repo: BaseRepository, job: dict, page_row: dict,
                              saved_symbols: list[dict], text_regions) -> None:
    """Stage 5: classify each resolved symbol into instrument/equipment/annotation,
    trace lines, infer relationships, persist everything."""
    project_id = job["project_id"]
    page_id = page_row["id"]

    instruments, equipment, annotations = [], [], []
    entities_for_relations: list[Entity] = []

    for sym in saved_symbols:
        bbox = (sym["bbox_x1"], sym["bbox_y1"], sym["bbox_x2"], sym["bbox_y2"])
        classification = classify_symbol(sym["class_name"], sym.get("extracted_text"))
        category = classification.pop("category")

        if category == "instrument":
            tag = classification.get("full_tag") or sym.get("extracted_text")
            inst = {
                "symbol_id": sym["id"], "tag": tag,
                "isa_type_code": classification.get("isa_type_code"),
                "instrument_type": sym["class_name"], "location": "field",
                "connected_to": [], "attributes": {},
            }
            instruments.append(inst)
        elif category == "equipment":
            eq = {
                "symbol_id": sym["id"], "tag": classification.get("tag") or sym.get("extracted_text"),
                "equipment_type": classification.get("equipment_type", sym["class_name"]),
                "bbox": {"x1": bbox[0], "y1": bbox[1], "x2": bbox[2], "y2": bbox[3]}, "attributes": {},
            }
            equipment.append(eq)
        elif category == "annotation" and sym.get("extracted_text"):
            annotations.append({
                "text": sym["extracted_text"],
                "bbox": {"x1": bbox[0], "y1": bbox[1], "x2": bbox[2], "y2": bbox[3]},
            })

    saved_instruments = repo.save_instruments(project_id, page_id, instruments) if instruments else []
    saved_equipment = repo.save_equipment(project_id, page_id, equipment) if equipment else []
    saved_annotations = repo.save_annotations(project_id, page_id, annotations) if annotations else []

    symbols_by_id = {s["id"]: s for s in saved_symbols}
    for inst, row in zip(instruments, saved_instruments):
        src_symbol = symbols_by_id.get(inst["symbol_id"])
        bbox = (
            (src_symbol["bbox_x1"], src_symbol["bbox_y1"], src_symbol["bbox_x2"], src_symbol["bbox_y2"])
            if src_symbol else (0.0, 0.0, 0.0, 0.0)
        )
        entities_for_relations.append(Entity(
            id=row["id"], entity_type="instrument", class_name=inst["instrument_type"],
            bbox=bbox, tag=row.get("tag"),
        ))
    for eq, row in zip(equipment, saved_equipment):
        entities_for_relations.append(Entity(
            id=row["id"], entity_type="equipment", class_name=eq["equipment_type"],
            bbox=(eq["bbox"]["x1"], eq["bbox"]["y1"], eq["bbox"]["x2"], eq["bbox"]["y2"]),
            tag=row.get("tag"),
        ))

    # Line tracing + endpoint snapping to entities
    symbol_bboxes = [(s["bbox_x1"], s["bbox_y1"], s["bbox_x2"], s["bbox_y2"]) for s in saved_symbols]
    traced = trace_lines(page_row["image_path"], symbol_bboxes)
    traced = snap_endpoints_to_entities(traced, entities_for_relations)

    line_rows = [{
        "line_number": None, "line_type": t.line_type,
        "from_tag": next((e.tag for e in entities_for_relations if e.id == t.from_entity_id), None),
        "to_tag": next((e.tag for e in entities_for_relations if e.id == t.to_entity_id), None),
        "path_points": [list(p) for p in t.path_points],
        "attributes": {},
    } for t in traced]
    saved_lines = repo.save_lines(project_id, page_id, line_rows) if line_rows else []

    line_dicts_for_relations = [{
        "from_entity_id": t.from_entity_id, "to_entity_id": t.to_entity_id,
    } for t in traced]

    rule_based = infer_relationships(entities_for_relations, line_dicts_for_relations)
    refinement = maybe_refine_with_gnn(entities_for_relations, rule_based)
    if refinement.relationships:
        repo.save_relationships(project_id, page_id, refinement.relationships)

    logger.info("page_structured", extra={"context": {
        "page_id": page_id, "instruments": len(saved_instruments), "equipment": len(saved_equipment),
        "lines": len(saved_lines), "relationships": len(refinement.relationships),
    }})


def render_and_register_pages(repo: BaseRepository, job: dict, pdf_paths: list[str], settings) -> list[dict]:
    """Stage 1 for every uploaded PDF, registering each page as a Page row."""
    all_pages = []
    for pdf_path in pdf_paths:
        rendered = render_pdf_to_images(pdf_path, settings.UPLOAD_DIR, dpi=settings.RENDER_DPI)
        for r in rendered:
            page_row = repo.create_page(
                job_id=job["id"], project_id=job["project_id"], page_number=r.page_number,
                source_filename=os.path.basename(pdf_path), image_path=r.image_path,
                width_px=r.width_px, height_px=r.height_px,
            )
            all_pages.append(page_row)
    return all_pages
