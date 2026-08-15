"""
SQLAlchemy ORM models. These map 1:1 onto the "minimum tables" list from the
spec: projects, pages, symbols, instruments, equipment, lines, relationships,
symbol_dictionary, extraction_jobs, unknown_symbols.

Shared across MSSQLRepository / PostgresRepository / MySQLRepository — the
SQL dialect differences are handled by the SQLAlchemy engine, not by these
model definitions. Mongo/Neo4j repositories do NOT use this file; they map
extraction results onto documents / graph nodes directly (see db/mongo_repo.py
and db/neo4j_repo.py).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, ForeignKey, Text, JSON,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)

    pages = relationship("Page", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("ExtractionJob", back_populates="project", cascade="all, delete-orphan")


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    status = Column(String(32), default="queued", index=True)  # queued|running|paused_unknown_symbol|completed|failed
    progress_pct = Column(Float, default=0.0)
    confidence_threshold = Column(Float, default=0.75)
    auto_learn_unknowns = Column(Boolean, default=False)
    source_filenames = Column(JSON, default=list)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    project = relationship("Project", back_populates="jobs")
    pages = relationship("Page", back_populates="job", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id = Column(String(36), primary_key=True, default=_uuid)
    job_id = Column(String(36), ForeignKey("extraction_jobs.id"), nullable=False, index=True)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    source_filename = Column(String(512), nullable=False)
    image_path = Column(String(1024), nullable=False)
    width_px = Column(Integer, nullable=True)
    height_px = Column(Integer, nullable=True)
    status = Column(String(32), default="pending")  # pending|processing|paused|completed|failed

    job = relationship("ExtractionJob", back_populates="pages")
    project = relationship("Project", back_populates="pages")
    symbols = relationship("Symbol", back_populates="page", cascade="all, delete-orphan")


class Symbol(Base):
    """A single detected symbol instance on a page (a raw CV detection)."""
    __tablename__ = "symbols"

    id = Column(String(36), primary_key=True, default=_uuid)
    page_id = Column(String(36), ForeignKey("pages.id"), nullable=False, index=True)
    class_name = Column(String(128), nullable=False, index=True)  # e.g. "control_valve", "unknown"
    confidence = Column(Float, nullable=False)
    bbox_x1 = Column(Float, nullable=False)
    bbox_y1 = Column(Float, nullable=False)
    bbox_x2 = Column(Float, nullable=False)
    bbox_y2 = Column(Float, nullable=False)
    extracted_text = Column(String(512), nullable=True)  # e.g. tag "FIC-101"
    is_unknown = Column(Boolean, default=False)
    resolved_from_unknown_id = Column(String(36), nullable=True)

    page = relationship("Page", back_populates="symbols")


class Equipment(Base):
    __tablename__ = "equipment"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    page_id = Column(String(36), ForeignKey("pages.id"), nullable=False, index=True)
    symbol_id = Column(String(36), ForeignKey("symbols.id"), nullable=True)
    tag = Column(String(128), nullable=True, index=True)
    equipment_type = Column(String(128), nullable=True, index=True)  # pump, vessel, exchanger...
    bbox_x1 = Column(Float, nullable=True)
    bbox_y1 = Column(Float, nullable=True)
    bbox_x2 = Column(Float, nullable=True)
    bbox_y2 = Column(Float, nullable=True)
    attributes = Column(JSON, default=dict)


class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    page_id = Column(String(36), ForeignKey("pages.id"), nullable=False, index=True)
    symbol_id = Column(String(36), ForeignKey("symbols.id"), nullable=True)
    tag = Column(String(128), nullable=True, index=True)  # e.g. FIC-101
    isa_type_code = Column(String(16), nullable=True, index=True)     # e.g. FIC, PT, LSH
    instrument_type = Column(String(128), nullable=True)  # human-readable
    location = Column(String(64), nullable=True)          # field | panel | dcs (from ISA-5.1 bubble style)
    connected_to = Column(JSON, default=list)              # list of tag strings, refined by relationship stage
    attributes = Column(JSON, default=dict)


class Line(Base):
    """A process, instrument-signal, or utility line/pipe."""
    __tablename__ = "lines"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    page_id = Column(String(36), ForeignKey("pages.id"), nullable=False, index=True)
    line_number = Column(String(128), nullable=True, index=True)
    line_type = Column(String(64), nullable=True, index=True)  # process | pneumatic_signal | electrical_signal | software_link
    from_tag = Column(String(128), nullable=True, index=True)
    to_tag = Column(String(128), nullable=True, index=True)
    path_points = Column(JSON, default=list)  # [[x,y], ...] polyline in page pixel space
    attributes = Column(JSON, default=dict)


class Annotation(Base):
    """Free-text notes / callouts that aren't tags belonging to a symbol."""
    __tablename__ = "annotations"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    page_id = Column(String(36), ForeignKey("pages.id"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    bbox_x1 = Column(Float, nullable=True)
    bbox_y1 = Column(Float, nullable=True)
    bbox_x2 = Column(Float, nullable=True)
    bbox_y2 = Column(Float, nullable=True)


class RelationshipEdge(Base):
    """An inferred edge between two entities (symbols/instruments/equipment/lines)."""
    __tablename__ = "relationships"
    __table_args__ = (
        # Speeds up "all edges touching entity X" lookups (graph traversal,
        # duplicate-tag/relationship checks) regardless of which side X is on.
        Index("ix_relationships_source_lookup", "source_entity_id", "source_entity_type"),
        Index("ix_relationships_target_lookup", "target_entity_id", "target_entity_type"),
        Index("ix_relationships_project_page", "project_id", "page_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    page_id = Column(String(36), ForeignKey("pages.id"), nullable=False)
    source_entity_id = Column(String(36), nullable=False)
    source_entity_type = Column(String(32), nullable=False)  # symbol|instrument|equipment|line
    target_entity_id = Column(String(36), nullable=False)
    target_entity_type = Column(String(32), nullable=False)
    relation_type = Column(String(32), nullable=False, index=True)  # connected_to|controls|measures|belongs_to
    confidence = Column(Float, default=1.0)
    inferred_by = Column(String(32), default="rule_based", index=True)  # rule_based|gnn


class SymbolDictionary(Base):
    """
    Known symbol categories, including user-added ones from the
    human-in-the-loop labeling flow. Acts as a lookup / template-matching
    table so previously-labeled unknowns are recognized without a human
    the next time they appear.
    """
    __tablename__ = "symbol_dictionary"
    __table_args__ = (UniqueConstraint("category_name", name="uq_symbol_dictionary_category"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    category_name = Column(String(128), nullable=False, index=True)
    source = Column(String(32), default="builtin", index=True)  # builtin|user_labeled
    isa_type_code = Column(String(16), nullable=True, index=True)
    description = Column(Text, nullable=True)
    reference_crop_path = Column(String(1024), nullable=True)
    shape_signature = Column(JSON, nullable=True)  # cheap geometric signature for re-matching, see services/cv
    attributes_schema = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now)


class UnknownSymbol(Base):
    """
    A detection that fell below confidence_threshold or came back as class
    'unknown'. Lives here while awaiting a human label; on labeling it's
    resolved and the originating Symbol row is updated.
    """
    __tablename__ = "unknown_symbols"

    id = Column(String(36), primary_key=True, default=_uuid)
    job_id = Column(String(36), ForeignKey("extraction_jobs.id"), nullable=False, index=True)
    page_id = Column(String(36), ForeignKey("pages.id"), nullable=False, index=True)
    symbol_id = Column(String(36), ForeignKey("symbols.id"), nullable=True)
    page_number = Column(Integer, nullable=False)
    bbox_x1 = Column(Float, nullable=False)
    bbox_y1 = Column(Float, nullable=False)
    bbox_x2 = Column(Float, nullable=False)
    bbox_y2 = Column(Float, nullable=False)
    crop_image_path = Column(String(1024), nullable=False)
    surrounding_text = Column(Text, nullable=True)
    original_confidence = Column(Float, nullable=True)
    status = Column(String(32), default="pending", index=True)  # pending|labeled
    user_provided_category = Column(String(128), nullable=True)
    user_provided_attributes = Column(JSON, default=dict)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)


# ---------------------------------------------------------------------------
# VLM expert-analyst tables — the exact four-table contract from
# app/schemas/vlm_schemas.py (PidExtractionResult), plus its unknown-symbols
# list. Deliberately separate from Instrument/Equipment/Line above: those
# belong to the CV+OCR pipeline (job_runner -> symbol_detector -> GNN) and
# use a different, narrower field set. These VlmXxx tables are what the
# single-shot VLM PDF-upload workflow (app/services/vlm/pdf_pipeline.py,
# app/api/routes/vlm_extraction_db.py) reads and writes, and what the
# editable-table frontend (frontend/index.html) works against directly.
# ---------------------------------------------------------------------------

class VlmExtractionRun(Base):
    """One PDF upload processed through the VLM pipeline."""
    __tablename__ = "vlm_extraction_runs"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    source_filename = Column(String(512), nullable=False)
    page_count = Column(Integer, default=0)
    model_used = Column(String(128), nullable=True)
    status = Column(String(32), default="completed")  # completed|failed
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)


class VlmInstrument(Base):
    __tablename__ = "vlm_instruments"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("vlm_extraction_runs.id"), nullable=True, index=True)
    instrument_tag = Column(String(128), nullable=True, index=True)
    instrument_type = Column(String(128), nullable=True)
    identification = Column(String(255), nullable=True)
    location = Column(String(64), nullable=True)
    connected_to = Column(JSON, default=list)
    page_number = Column(Integer, nullable=True)
    bbox_x1 = Column(Float, nullable=True)
    bbox_y1 = Column(Float, nullable=True)
    bbox_x2 = Column(Float, nullable=True)
    bbox_y2 = Column(Float, nullable=True)
    attributes = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class VlmEquipment(Base):
    __tablename__ = "vlm_equipment"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("vlm_extraction_runs.id"), nullable=True, index=True)
    equipment_tag = Column(String(128), nullable=True, index=True)
    equipment_type = Column(String(128), nullable=True)
    identification = Column(String(255), nullable=True)
    capacity = Column(String(128), nullable=True)
    other_data = Column(JSON, default=dict)  # material, design_pressure, design_temperature, power, notes
    page_number = Column(Integer, nullable=True)
    bbox_x1 = Column(Float, nullable=True)
    bbox_y1 = Column(Float, nullable=True)
    bbox_x2 = Column(Float, nullable=True)
    bbox_y2 = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class VlmPipeRun(Base):
    __tablename__ = "vlm_pipe_runs"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("vlm_extraction_runs.id"), nullable=True, index=True)
    pipe_run_tag = Column(String(128), nullable=True, index=True)
    size = Column(String(32), nullable=True)
    fluid_code = Column(String(32), nullable=True)
    pipe_material_spec = Column(String(128), nullable=True)
    insulation = Column(String(64), nullable=True)
    insulation_thickness = Column(String(32), nullable=True)
    other_information = Column(JSON, default=dict)  # piping_class, from, to, design_pressure, design_temperature, notes
    page_number = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class VlmPipingComponent(Base):
    __tablename__ = "vlm_piping_components"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("vlm_extraction_runs.id"), nullable=True, index=True)
    component_tag = Column(String(128), nullable=True, index=True)
    piping_component_type = Column(String(128), nullable=True)
    connected_pipe_run = Column(String(128), nullable=True)
    size = Column(String(32), nullable=True)
    other_information = Column(JSON, default=dict)  # rating, material, end_connection, notes
    page_number = Column(Integer, nullable=True)
    bbox_x1 = Column(Float, nullable=True)
    bbox_y1 = Column(Float, nullable=True)
    bbox_x2 = Column(Float, nullable=True)
    bbox_y2 = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class VlmUnknownSymbol(Base):
    """
    A symbol/icon the VLM saw on a page but could not confidently classify
    into any of the four tables above. Distinct from the CV-pipeline's
    UnknownSymbol table (which is keyed to a confidence-threshold miss on a
    YOLO detection, not a VLM read of a whole page) — this one is keyed to
    a VlmExtractionRun and carries an actual crop image cut from the
    rendered page for the teaching UI, saved by the API layer (the VLM
    itself only reports a bbox + description, not an image).
    """
    __tablename__ = "vlm_unknown_symbols"

    id = Column(String(36), primary_key=True, default=_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("vlm_extraction_runs.id"), nullable=True, index=True)
    page_number = Column(Integer, nullable=True)
    bbox_x1 = Column(Float, nullable=True)
    bbox_y1 = Column(Float, nullable=True)
    bbox_x2 = Column(Float, nullable=True)
    bbox_y2 = Column(Float, nullable=True)
    description = Column(Text, nullable=True)  # what the model saw / thinks it might be
    nearby_text = Column(String(255), nullable=True)
    crop_image_path = Column(String(1024), nullable=True)
    status = Column(String(32), default="pending", index=True)  # pending|resolved
    resolved_category = Column(String(128), nullable=True)
    resolved_target_table = Column(String(32), nullable=True)  # instruments|equipment|pipe_runs|piping_components
    resolved_row_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=_now)
    resolved_at = Column(DateTime, nullable=True)
