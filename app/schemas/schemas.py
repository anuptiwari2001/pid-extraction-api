"""Pydantic schemas — request/response contracts for the API layer."""
from datetime import datetime
from typing import Optional, Any

from pydantic import BaseModel, Field


# ---------- Jobs ----------

class ExtractionJobCreateResponse(BaseModel):
    job_id: str
    project_id: str
    status: str
    pages_queued: int
    message: str = "Extraction job created."


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress_pct: float
    error_message: Optional[str] = None
    pending_unknown_symbols: int = 0
    updated_at: datetime


class BBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class SymbolOut(BaseModel):
    id: str
    class_name: str
    confidence: float
    bbox: BBox
    extracted_text: Optional[str] = None
    is_unknown: bool = False


class InstrumentOut(BaseModel):
    id: str
    tag: Optional[str] = None
    isa_type_code: Optional[str] = None
    instrument_type: Optional[str] = None
    location: Optional[str] = None
    connected_to: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EquipmentOut(BaseModel):
    id: str
    tag: Optional[str] = None
    equipment_type: Optional[str] = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class LineOut(BaseModel):
    id: str
    line_number: Optional[str] = None
    line_type: Optional[str] = None
    from_tag: Optional[str] = None
    to_tag: Optional[str] = None
    path_points: list[list[float]] = Field(default_factory=list)


class AnnotationOut(BaseModel):
    id: str
    text: str


class RelationshipOut(BaseModel):
    id: str
    source_entity_id: str
    source_entity_type: str
    target_entity_id: str
    target_entity_type: str
    relation_type: str
    confidence: float
    inferred_by: str


class GraphNode(BaseModel):
    id: str
    type: str
    label: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    relation_type: str
    confidence: float


class GraphExport(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class PageResult(BaseModel):
    page_id: str
    page_number: int
    source_filename: str
    symbols: list[SymbolOut]
    instruments: list[InstrumentOut]
    equipment: list[EquipmentOut]
    lines: list[LineOut]
    annotations: list[AnnotationOut]


class JobResultResponse(BaseModel):
    job_id: str
    project_id: str
    status: str
    pages: list[PageResult]
    relationships: list[RelationshipOut]
    graph: GraphExport


# ---------- Unknown symbol / human-in-the-loop ----------

class UnknownSymbolOut(BaseModel):
    unknown_symbol_id: str
    job_id: str
    page_id: str
    page_number: int
    bbox: BBox
    image_crop_base64: str
    surrounding_context_text: Optional[str] = None
    original_confidence: Optional[float] = None


class LabelUnknownSymbolRequest(BaseModel):
    unknown_symbol_id: str = Field(..., description="ID returned in the unknown-symbol payload")
    user_provided_category: str
    isa_type_code: Optional[str] = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    resume_processing: bool = True


class LabelUnknownSymbolResponse(BaseModel):
    unknown_symbol_id: str
    category_name: str
    added_to_dictionary: bool
    job_resumed: bool
    job_status: str


class SymbolDictionaryEntry(BaseModel):
    id: str
    category_name: str
    source: str
    isa_type_code: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime


# ---------- DB connection management ----------

class ConnectDbRequest(BaseModel):
    database_type: str = Field(..., description="mssql|postgres|mysql|mongo|neo4j")
    connection_string: Optional[str] = None
    neo4j_uri: Optional[str] = None
    neo4j_user: Optional[str] = None
    neo4j_password: Optional[str] = None


class ConnectDbResponse(BaseModel):
    database_type: str
    connected: bool
    message: str


class HealthResponse(BaseModel):
    status: str
    database_type: str
    database_connected: bool
    version: str = "1.0.0"
