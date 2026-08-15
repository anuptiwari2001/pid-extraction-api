"""
Schemas for the VLM expert-analyst feature (app/services/vlm/pid_expert_analyst.py).

Output is four engineering-data tables — instruments, equipment, pipe_runs,
piping_components — extracted directly from a page image by the vision
model. Kept separate from app/schemas/schemas.py because this is a
different contract: schemas.py describes the CV/OCR pipeline's DB-backed
output, this describes a single-shot vision-model pass over one image (or
a region of one) that never touches symbol_detector/OCR/GNN.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMPTY_BBOX = [0.0, 0.0, 0.0, 0.0]


class VlmInstrument(BaseModel):
    instrument_tag: Optional[str] = Field(default=None, description="e.g. PT-101A")
    instrument_type: Optional[str] = Field(default=None, description="e.g. Pressure Transmitter")
    identification: Optional[str] = Field(default=None, description="ISA-5.1 letters + function, e.g. 'PT = Pressure Transmitter'")
    location: Optional[str] = Field(default=None, description="local / panel / shared")
    connected_to: list[str] = Field(default_factory=list, description="Equipment or line tags this instrument is connected to")
    page_number: Optional[int] = None
    bbox: list[float] = Field(default_factory=lambda: list(_EMPTY_BBOX))
    attributes: dict = Field(default_factory=dict)

    @field_validator("bbox")
    @classmethod
    def _bbox_len(cls, v: list[float]) -> list[float]:
        return v if len(v) == 4 else list(_EMPTY_BBOX)


class VlmEquipmentOtherData(BaseModel):
    material: Optional[str] = None
    design_pressure: Optional[str] = None
    design_temperature: Optional[str] = None
    power: Optional[str] = None
    notes: Optional[str] = None


class VlmEquipment(BaseModel):
    equipment_tag: Optional[str] = Field(default=None, description="e.g. P-101A")
    equipment_type: Optional[str] = Field(default=None, description="e.g. Centrifugal Pump")
    identification: Optional[str] = Field(default=None, description="Full description")
    capacity: Optional[str] = Field(default=None, description="e.g. 50 m3/h or 200 GPM, if available")
    other_data: VlmEquipmentOtherData = Field(default_factory=VlmEquipmentOtherData)
    page_number: Optional[int] = None
    bbox: list[float] = Field(default_factory=lambda: list(_EMPTY_BBOX))

    @field_validator("bbox")
    @classmethod
    def _bbox_len(cls, v: list[float]) -> list[float]:
        return v if len(v) == 4 else list(_EMPTY_BBOX)


class VlmPipeRunOtherInformation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    piping_class: Optional[str] = None
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    design_pressure: Optional[str] = None
    design_temperature: Optional[str] = None
    notes: Optional[str] = None


class VlmPipeRun(BaseModel):
    pipe_run_tag: Optional[str] = Field(default=None, description='e.g. 4"-P-1012-A1A')
    size: Optional[str] = Field(default=None, description='e.g. 4"')
    fluid_code: Optional[str] = Field(default=None, description="e.g. P (Process), CW (Cooling Water), etc.")
    pipe_material_spec: Optional[str] = Field(default=None, description="e.g. A106 Gr.B / CS")
    insulation: Optional[str] = Field(default=None, description="yes/no + type if available")
    insulation_thickness: Optional[str] = Field(default=None, description="e.g. 50 mm")
    other_information: VlmPipeRunOtherInformation = Field(default_factory=VlmPipeRunOtherInformation)
    page_number: Optional[int] = None
    # No bbox: a pipe run is a whole line (often spanning most of the page
    # and multiple segments), not a single point symbol, so a single bbox
    # isn't part of this table per spec — trace geometry belongs to the CV
    # pipeline's line_tracer, not this per-page VLM pass.


class VlmPipingComponentOtherInformation(BaseModel):
    rating: Optional[str] = Field(default=None, description="e.g. 150#")
    material: Optional[str] = None
    end_connection: Optional[str] = Field(default=None, description="Flanged / BW / SW")
    notes: Optional[str] = None


class VlmPipingComponent(BaseModel):
    component_tag: Optional[str] = Field(default=None, description="e.g. V-1012 or XV-101")
    piping_component_type: Optional[str] = Field(default=None, description="e.g. Gate Valve, Check Valve, Reducer, Elbow, Strainer")
    connected_pipe_run: Optional[str] = Field(default=None, description="pipe_run_tag it belongs to")
    size: Optional[str] = Field(default=None, description='e.g. 4"')
    other_information: VlmPipingComponentOtherInformation = Field(default_factory=VlmPipingComponentOtherInformation)
    page_number: Optional[int] = None
    bbox: list[float] = Field(default_factory=lambda: list(_EMPTY_BBOX))

    @field_validator("bbox")
    @classmethod
    def _bbox_len(cls, v: list[float]) -> list[float]:
        return v if len(v) == 4 else list(_EMPTY_BBOX)


class VlmUnknownSymbol(BaseModel):
    """
    A symbol/icon the model could see but could not confidently classify
    into any of the four tables (not a standard ISA-5.1/PIP symbol it
    recognizes, or too ambiguous/illegible to tag with confidence). The
    model reports only location + description; the API layer is
    responsible for actually cutting the crop image from the rendered page
    (see app/services/vlm/pdf_pipeline.py) since the model has no way to
    return image bytes itself.
    """
    description: Optional[str] = Field(default=None, description="What the symbol looks like / what it might be, in the model's own words")
    nearby_text: Optional[str] = Field(default=None, description="Any tag/text near the symbol, if legible")
    possible_category: Optional[str] = Field(default=None, description="Best guess at a category, if any (e.g. 'possibly a special valve')")
    page_number: Optional[int] = None
    bbox: list[float] = Field(default_factory=lambda: list(_EMPTY_BBOX))

    @field_validator("bbox")
    @classmethod
    def _bbox_len(cls, v: list[float]) -> list[float]:
        return v if len(v) == 4 else list(_EMPTY_BBOX)


class PidExtractionResult(BaseModel):
    """The exact four-table contract, plus unknown symbols and a little run provenance."""

    model_config = ConfigDict(protected_namespaces=())

    instruments: list[VlmInstrument] = Field(default_factory=list)
    equipment: list[VlmEquipment] = Field(default_factory=list)
    pipe_runs: list[VlmPipeRun] = Field(default_factory=list)
    piping_components: list[VlmPipingComponent] = Field(default_factory=list)
    unknown_symbols: list[VlmUnknownSymbol] = Field(default_factory=list, description="Symbols seen but not confidently classified into any table above")

    # Run metadata — not part of the four required tables, purely for
    # observability/debugging; always present but never populated by the
    # model itself.
    region_analyzed: Optional[list[float]] = Field(default=None, description="[x1,y1,x2,y2] of the sub-region analyzed, in full-page pixel coords; null if the full page was analyzed.")
    model_used: Optional[str] = None
    parse_attempts: int = 1
    extraction_notes: Optional[str] = Field(default=None, description="Set only when extraction degraded or failed (e.g. VLM unreachable) — the four arrays above are empty in that case.")
