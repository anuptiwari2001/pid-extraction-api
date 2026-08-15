"""
SQL persistence for the VLM expert-analyst four-table contract
(vlm_instruments / vlm_equipment / vlm_pipe_runs / vlm_piping_components)
plus vlm_unknown_symbols and vlm_extraction_runs — see the VlmXxx models in
app/models/orm.py for the schema.

Deliberately NOT routed through BaseRepository/SQLRepository: that
abstraction exists so the CV+OCR job pipeline can run against
mssql/postgres/mysql/mongo/neo4j interchangeably, but this feature's
tables are relational-only (they're literally "an editable spreadsheet
row per extracted item" — a natural fit for SQL, not for Mongo/Neo4j) and
the user asked specifically for MSSQL. Reusing the *engine* the active
repository already holds (when it's a SQL backend) means POST /connect-db
still works as the one place to configure the connection; this module just
adds its own tables/queries on top of that same connection.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import get_settings
from app.core.errors import ValidationErrorApp, NotFoundError
from app.core.logging_config import get_logger
from app.db.factory import get_repository
from app.models.orm import (
    Base, VlmExtractionRun, VlmInstrument, VlmEquipment, VlmPipeRun,
    VlmPipingComponent, VlmUnknownSymbol,
)
from app.schemas.vlm_schemas import PidExtractionResult

logger = get_logger(__name__)

TABLE_MODELS = {
    "instruments": VlmInstrument,
    "equipment": VlmEquipment,
    "pipe_runs": VlmPipeRun,
    "piping_components": VlmPipingComponent,
}

_BBOX_TABLES = {"instruments", "equipment", "piping_components"}  # pipe_runs has no bbox, per spec

_standalone_engine = None
_standalone_sessionmaker: Optional[sessionmaker] = None


def _sql_dialects() -> tuple[str, ...]:
    return ("mssql", "postgres", "mysql")


def _get_sessionmaker() -> sessionmaker:
    """
    Prefers the active repository's own engine (set up via POST /connect-db
    or DATABASE_TYPE/DATABASE_URL at startup) when it's a SQL backend, so
    there's exactly one connection pool per SQL target. Falls back to a
    lazily-built standalone engine from settings.DATABASE_URL only if the
    active repository is mongo/neo4j but settings still point at a SQL
    DATABASE_URL (an uncommon mixed setup) — otherwise raises, since there's
    nothing sensible to connect this feature to.
    """
    global _standalone_engine, _standalone_sessionmaker

    repo = get_repository()
    if hasattr(repo, "SessionLocal") and hasattr(repo, "engine") and repo.engine is not None:
        _ensure_vlm_schema(repo.engine)
        return repo.SessionLocal

    settings = get_settings()
    if settings.DATABASE_TYPE not in _sql_dialects():
        raise ValidationErrorApp(
            "The VLM extraction tables require a SQL database (mssql, postgres, or mysql). "
            f"Current active backend is '{settings.DATABASE_TYPE}'. Connect a SQL database via POST /connect-db first."
        )

    if _standalone_sessionmaker is None:
        _standalone_engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)
        _standalone_sessionmaker = sessionmaker(bind=_standalone_engine, expire_on_commit=False, future=True)
        _ensure_vlm_schema(_standalone_engine)
    return _standalone_sessionmaker


def _ensure_vlm_schema(engine) -> None:
    Base.metadata.create_all(
        bind=engine,
        tables=[
            VlmExtractionRun.__table__, VlmInstrument.__table__, VlmEquipment.__table__,
            VlmPipeRun.__table__, VlmPipingComponent.__table__, VlmUnknownSymbol.__table__,
        ],
        checkfirst=True,
    )


def _row_to_dict(obj) -> dict:
    d = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    if "bbox_x1" in d:
        d["bbox"] = [d.pop("bbox_x1") or 0.0, d.pop("bbox_y1") or 0.0, d.pop("bbox_x2") or 0.0, d.pop("bbox_y2") or 0.0]
    return d


def _bbox_kwargs(bbox: list[float]) -> dict:
    if not bbox or len(bbox) != 4:
        bbox = [0.0, 0.0, 0.0, 0.0]
    return {"bbox_x1": bbox[0], "bbox_y1": bbox[1], "bbox_x2": bbox[2], "bbox_y2": bbox[3]}


# --------------------------------------------------------------------- #
# Save a fresh extraction result
# --------------------------------------------------------------------- #

def save_extraction(
    project_id: str, source_filename: str, result: PidExtractionResult,
    page_count: int, model_used: Optional[str], unknown_crop_paths: list[Optional[str]],
    notes: Optional[str] = None,
) -> dict:
    """Persists a full PidExtractionResult (all four tables + unknown_symbols) under a new run, returning the saved rows with their ids."""
    SessionLocal = _get_sessionmaker()
    with SessionLocal() as session:  # type: Session
        run = VlmExtractionRun(
            project_id=project_id, source_filename=source_filename, page_count=page_count,
            model_used=model_used, status="completed", notes=notes,
        )
        session.add(run)
        session.flush()

        saved: dict[str, list[dict]] = {"instruments": [], "equipment": [], "pipe_runs": [], "piping_components": [], "unknown_symbols": []}

        for item in result.instruments:
            row = VlmInstrument(
                project_id=project_id, run_id=run.id, instrument_tag=item.instrument_tag,
                instrument_type=item.instrument_type, identification=item.identification,
                location=item.location, connected_to=item.connected_to, page_number=item.page_number,
                attributes=item.attributes, **_bbox_kwargs(item.bbox),
            )
            session.add(row)
            session.flush()
            saved["instruments"].append(_row_to_dict(row))

        for item in result.equipment:
            row = VlmEquipment(
                project_id=project_id, run_id=run.id, equipment_tag=item.equipment_tag,
                equipment_type=item.equipment_type, identification=item.identification,
                capacity=item.capacity, other_data=item.other_data.model_dump(), page_number=item.page_number,
                **_bbox_kwargs(item.bbox),
            )
            session.add(row)
            session.flush()
            saved["equipment"].append(_row_to_dict(row))

        for item in result.pipe_runs:
            row = VlmPipeRun(
                project_id=project_id, run_id=run.id, pipe_run_tag=item.pipe_run_tag, size=item.size,
                fluid_code=item.fluid_code, pipe_material_spec=item.pipe_material_spec,
                insulation=item.insulation, insulation_thickness=item.insulation_thickness,
                other_information=item.other_information.model_dump(by_alias=True), page_number=item.page_number,
            )
            session.add(row)
            session.flush()
            saved["pipe_runs"].append(_row_to_dict(row))

        for item in result.piping_components:
            row = VlmPipingComponent(
                project_id=project_id, run_id=run.id, component_tag=item.component_tag,
                piping_component_type=item.piping_component_type, connected_pipe_run=item.connected_pipe_run,
                size=item.size, other_information=item.other_information.model_dump(), page_number=item.page_number,
                **_bbox_kwargs(item.bbox),
            )
            session.add(row)
            session.flush()
            saved["piping_components"].append(_row_to_dict(row))

        for idx, item in enumerate(result.unknown_symbols):
            crop_path = unknown_crop_paths[idx] if idx < len(unknown_crop_paths) else None
            row = VlmUnknownSymbol(
                project_id=project_id, run_id=run.id, page_number=item.page_number,
                description=item.description, nearby_text=item.nearby_text,
                crop_image_path=crop_path, status="pending", **_bbox_kwargs(item.bbox),
            )
            session.add(row)
            session.flush()
            saved["unknown_symbols"].append(_row_to_dict(row))

        session.commit()
        saved["run"] = _row_to_dict(run)
        logger.info("vlm_extraction_saved", extra={"context": {
            "project_id": project_id, "run_id": run.id,
            "counts": {k: len(v) for k, v in saved.items() if k != "run"},
        }})
        return saved


# --------------------------------------------------------------------- #
# Read / edit
# --------------------------------------------------------------------- #

def get_project_extraction(project_id: str) -> dict:
    SessionLocal = _get_sessionmaker()
    with SessionLocal() as session:
        out = {}
        for name, model in TABLE_MODELS.items():
            rows = session.query(model).filter(model.project_id == project_id).order_by(model.page_number).all()
            out[name] = [_row_to_dict(r) for r in rows]
        unknowns = session.query(VlmUnknownSymbol).filter(VlmUnknownSymbol.project_id == project_id).order_by(VlmUnknownSymbol.page_number).all()
        out["unknown_symbols"] = [_row_to_dict(r) for r in unknowns]
        return out


def update_row(table_name: str, row_id: str, fields: dict[str, Any]) -> dict:
    """Partial update of one row — only keys present in `fields` are touched. Used by the editable-table UI to save a single cell/row edit."""
    model = TABLE_MODELS.get(table_name)
    if model is None:
        raise ValidationErrorApp(f"Unknown table '{table_name}'. Valid tables: {sorted(TABLE_MODELS)}")

    SessionLocal = _get_sessionmaker()
    with SessionLocal() as session:
        row = session.get(model, row_id)
        if row is None:
            raise NotFoundError(f"Row {row_id} not found in {table_name}")

        bbox = fields.pop("bbox", None)
        if bbox is not None and table_name in _BBOX_TABLES:
            for k, v in _bbox_kwargs(bbox).items():
                setattr(row, k, v)

        column_names = {c.name for c in model.__table__.columns}
        for key, value in fields.items():
            if key in ("id", "project_id", "run_id"):
                continue  # identity columns are never editable from here
            if key in column_names:
                setattr(row, key, value)

        if hasattr(row, "updated_at"):
            row.updated_at = datetime.now(timezone.utc)

        session.commit()
        session.refresh(row)
        return _row_to_dict(row)


def delete_row(table_name: str, row_id: str) -> None:
    model = TABLE_MODELS.get(table_name)
    if model is None:
        raise ValidationErrorApp(f"Unknown table '{table_name}'. Valid tables: {sorted(TABLE_MODELS)}")

    SessionLocal = _get_sessionmaker()
    with SessionLocal() as session:
        row = session.get(model, row_id)
        if row is None:
            raise NotFoundError(f"Row {row_id} not found in {table_name}")
        session.delete(row)
        session.commit()


# --------------------------------------------------------------------- #
# Unknown symbols / teaching
# --------------------------------------------------------------------- #

def get_unknown_symbol(unknown_id: str) -> dict:
    SessionLocal = _get_sessionmaker()
    with SessionLocal() as session:
        row = session.get(VlmUnknownSymbol, unknown_id)
        if row is None:
            raise NotFoundError(f"Unknown symbol {unknown_id} not found")
        return _row_to_dict(row)


def resolve_unknown_symbol(unknown_id: str, category_name: str, target_table: str, fields: dict[str, Any]) -> dict:
    """
    Human-confirmed teaching outcome: creates a real row in the chosen
    target table (instruments/equipment/pipe_runs/piping_components) using
    `fields` (same shape as that table's editable columns), then marks the
    unknown_symbols row resolved and links it to the new row so the
    original crop/description stays traceable.
    """
    model = TABLE_MODELS.get(target_table)
    if model is None:
        raise ValidationErrorApp(f"Unknown target_table '{target_table}'. Valid tables: {sorted(TABLE_MODELS)}")

    SessionLocal = _get_sessionmaker()
    with SessionLocal() as session:
        unknown = session.get(VlmUnknownSymbol, unknown_id)
        if unknown is None:
            raise NotFoundError(f"Unknown symbol {unknown_id} not found")

        bbox = fields.pop("bbox", None)
        bbox_kwargs = _bbox_kwargs(bbox) if (bbox and target_table in _BBOX_TABLES) else (
            _bbox_kwargs([unknown.bbox_x1, unknown.bbox_y1, unknown.bbox_x2, unknown.bbox_y2]) if target_table in _BBOX_TABLES else {}
        )

        row_kwargs = {"project_id": unknown.project_id, "run_id": unknown.run_id, "page_number": fields.pop("page_number", unknown.page_number), **bbox_kwargs}
        column_names = {c.name for c in model.__table__.columns}
        for key, value in fields.items():
            if key in column_names and key not in row_kwargs:
                row_kwargs[key] = value

        new_row = model(**row_kwargs)
        session.add(new_row)
        session.flush()

        unknown.status = "resolved"
        unknown.resolved_category = category_name
        unknown.resolved_target_table = target_table
        unknown.resolved_row_id = new_row.id
        unknown.resolved_at = datetime.now(timezone.utc)

        session.commit()
        session.refresh(new_row)
        return {"unknown_symbol_id": unknown_id, "target_table": target_table, "new_row": _row_to_dict(new_row)}
