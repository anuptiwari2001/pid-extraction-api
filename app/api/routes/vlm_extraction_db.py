"""
The end-to-end "upload PDF -> VLM extraction -> editable DB tables ->
unknown-symbol teaching" workflow the simple /ui frontend drives.

This is the single surviving VLM router — the "just VLM, straight to
editable SQL rows" path: POST /vlm/extract-pdf renders and analyzes every
page of an uploaded PDF, classifies each item per ISA-5.1 (instruments) /
PIP (equipment, pipe runs, piping components) exactly as the four-table
contract in app/schemas/vlm_schemas.py specifies, and saves everything
straight to the right vlm_* table (see app/services/vlm/vlm_store.py).
Everything after that is plain CRUD, plus the AI-assisted teaching pair
for unknown symbols (POST /vlm/suggest-teaching to get an AI suggestion,
POST /vlm/teach-symbol to save a human-confirmed one — both formerly
lived in the now-removed vlm_analysis.py, folded in here so every VLM
endpoint lives under one router/tag).
"""
import base64
import json
import os
import shutil
import uuid
from typing import Any, Optional

from fastapi import APIRouter, UploadFile, File, Form, Query, Body, Depends

from app.core.config import get_settings
from app.core.errors import UnsupportedFileError, ValidationErrorApp
from app.core.logging_config import get_logger
from app.db.base_repository import BaseRepository
from app.api.deps import get_repo
from app.services.vlm.pdf_pipeline import analyze_pdf
from app.services.vlm import vlm_store
from app.services.vlm.teaching import suggest_teaching
from app.schemas.teaching_schemas import (
    TeachingSuggestionResult, TeachSymbolResponse, TeachingCategory, RecommendedTable,
)

router = APIRouter(prefix="/vlm", tags=["vlm-extraction-db"])
logger = get_logger(__name__)


@router.post("/extract-pdf")
async def extract_pdf(
    file: UploadFile = File(..., description="A P&ID PDF (one or more pages)"),
    project_id: Optional[str] = Query(default=None, description="Existing project to save into; a new one is created if omitted"),
    model: Optional[str] = Query(default=None, description="Override the configured VLM model for this run"),
    repo: BaseRepository = Depends(get_repo),
):
    """
    Upload a P&ID PDF: renders every page, runs the expert-analyst VLM pass
    on each, and saves everything straight into the editable vlm_* SQL
    tables (a SQL database — mssql/postgres/mysql — must be connected
    first via POST /connect-db; DATABASE_TYPE=mssql is the default).

    Returns the project_id/run_id plus every saved row (with ids) for the
    four tables and unknown_symbols, so the frontend can render the
    editable tables immediately without a second round-trip.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise UnsupportedFileError(f"'{file.filename}' is not a PDF.")

    if project_id:
        project = repo.get_project(project_id)
        if not project:
            raise ValidationErrorApp(f"project_id '{project_id}' does not exist.")
    else:
        project = repo.create_project(name=f"P&ID upload {uuid.uuid4().hex[:8]}")
        project_id = project["id"]

    settings = get_settings()
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    pdf_path = os.path.join(settings.UPLOAD_DIR, f"{uuid.uuid4().hex[:8]}_{file.filename}")
    with open(pdf_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    outcome = analyze_pdf(pdf_path, model=model)

    saved = vlm_store.save_extraction(
        project_id=project_id, source_filename=file.filename, result=outcome.result,
        page_count=outcome.page_count, model_used=outcome.model_used,
        unknown_crop_paths=outcome.unknown_symbol_crop_paths,
        notes="; ".join(outcome.notes) if outcome.notes else None,
    )

    logger.info("pdf_extraction_saved", extra={"context": {
        "project_id": project_id, "filename": file.filename, "pages": outcome.page_count,
    }})

    return {
        "project_id": project_id,
        "run_id": saved["run"]["id"],
        "page_count": outcome.page_count,
        "model_used": outcome.model_used,
        "notes": outcome.notes,
        "instruments": saved["instruments"],
        "equipment": saved["equipment"],
        "pipe_runs": saved["pipe_runs"],
        "piping_components": saved["piping_components"],
        "unknown_symbols": saved["unknown_symbols"],
    }


@router.get("/extraction/{project_id}")
def get_extraction(project_id: str):
    """Everything saved for a project: the four editable tables plus unknown_symbols (without crop image bytes — use the unknown-symbols endpoint below for those)."""
    return vlm_store.get_project_extraction(project_id)


@router.put("/extraction/{project_id}/{table_name}/{row_id}")
def update_extraction_row(project_id: str, table_name: str, row_id: str, fields: dict[str, Any] = Body(..., description="Partial set of column values to update, e.g. {\"instrument_tag\": \"PT-101B\"}")):
    """Saves an edit to a single row in one of the four tables — the editable-table 'Save' action."""
    return vlm_store.update_row(table_name, row_id, fields)


@router.delete("/extraction/{project_id}/{table_name}/{row_id}", status_code=204)
def delete_extraction_row(project_id: str, table_name: str, row_id: str):
    vlm_store.delete_row(table_name, row_id)


@router.get("/extraction/{project_id}/unknown-symbols")
def list_unknown_symbols(project_id: str, status: Optional[str] = Query(default=None, description="Filter: pending | resolved")):
    """Unknown symbols for a project, with each crop image inlined as base64 for the teaching UI."""
    data = vlm_store.get_project_extraction(project_id)
    items = data["unknown_symbols"]
    if status:
        items = [i for i in items if i.get("status") == status]

    out = []
    for item in items:
        crop_b64 = None
        crop_path = item.get("crop_image_path")
        if crop_path and os.path.exists(crop_path):
            with open(crop_path, "rb") as f:
                crop_b64 = base64.b64encode(f.read()).decode("utf-8")
        out.append({**item, "image_crop_base64": crop_b64})
    return out


@router.post("/extraction/{project_id}/unknown-symbols/{unknown_id}/resolve")
def resolve_unknown_symbol(
    project_id: str,
    unknown_id: str,
    category_name: str = Body(..., embed=True),
    target_table: str = Body(..., embed=True, description="instruments | equipment | pipe_runs | piping_components"),
    fields: dict[str, Any] = Body(default_factory=dict, embed=True, description="Column values for the new row in target_table, e.g. {\"instrument_type\": \"...\", \"instrument_tag\": \"...\"}"),
    repo: BaseRepository = Depends(get_repo),
):
    """
    Teach step, final save: creates a real row in the chosen table from the
    human-confirmed fields (typically pre-filled by POST /vlm/suggest-teaching
    and edited in the popup), marks the unknown symbol resolved, and — same
    as POST /vlm/teach-symbol — adds the symbol to symbol_dictionary so a
    visually similar one can be recognized automatically on a future upload.
    """
    result = vlm_store.resolve_unknown_symbol(unknown_id, category_name, target_table, dict(fields))

    try:
        unknown = vlm_store.get_unknown_symbol(unknown_id)
        crop_path = unknown.get("crop_image_path")
        if crop_path and os.path.exists(crop_path):
            repo.add_symbol_dictionary_entry(
                category_name=category_name, source="vlm_taught",
                description=unknown.get("description"), reference_crop_path=crop_path,
                attributes_schema={"recommended_table": target_table},
            )
    except Exception as exc:
        # Best-effort — the row is already saved and the unknown symbol is
        # already resolved; failing to also register it in the reusable
        # symbol_dictionary shouldn't fail the whole request.
        logger.warning("symbol_dictionary_update_failed", extra={"context": {"error": str(exc)}})

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
    symbol_dictionary here. The human reviews/edits the fields and submits
    the final version to POST /vlm/teach-symbol (or, if the unknown symbol
    is already a saved row from /vlm/extract-pdf, to
    POST /vlm/extraction/{project_id}/unknown-symbols/{unknown_id}/resolve),
    which does the actual save.

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
    Unknown/non-standard symbol teaching — final human-confirmed save step,
    for a crop that isn't (or isn't yet) a saved vlm_unknown_symbols row.

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
       for the same shape.
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
