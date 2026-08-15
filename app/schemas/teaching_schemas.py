"""
Schemas for the unknown/non-standard symbol "teaching" workflow.

Two related but distinct things live here:

1. TeachingSuggestion — what a VLM proposes when shown a crop it (or the
   symbol-detection pass) couldn't confidently classify against ISA-5.1/
   PIP. This is a SUGGESTION only, meant to pre-fill a human review popup —
   it is never written to the database on its own.

2. TeachSymbolRequest/Response — the final, human-confirmed submission
   (matching the Human-in-the-Loop popup fields: category, name/type, tag
   format, description, target table) that actually gets stored in
   symbol_dictionary. A human can submit this from scratch, or by
   confirming/editing an AI TeachingSuggestion first — either way the
   write path is the same.
"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TeachingCategory = Literal["instrument", "equipment", "piping_component", "pipe_run", "other"]
RecommendedTable = Literal["instruments", "equipment", "piping_components", "pipe_runs"]


class TeachingSuggestion(BaseModel):
    """Exactly the JSON contract the VLM is prompted to return."""

    action: Literal["teach"] = "teach"
    new_class_name: str = Field(..., description="Exact name to store, e.g. 'Special Ball Valve - Fire Safe'")
    category: TeachingCategory
    standard_reference: str = Field(default="None", description="'None' / 'Custom' / 'Company Standard'")
    description: str
    recommended_table: RecommendedTable
    default_attributes: dict[str, Any] = Field(default_factory=dict)
    isa_equivalent: Optional[str] = Field(default=None, description="Closest ISA-5.1 code, if any")
    pip_equivalent: Optional[str] = Field(default=None, description="Closest PIP symbol, if any")


class TeachingSuggestionResult(BaseModel):
    """API response wrapper — the suggestion plus run provenance, or a note explaining why there isn't one."""

    model_config = ConfigDict(protected_namespaces=())

    suggestion: Optional[TeachingSuggestion] = None
    model_used: Optional[str] = None
    parse_attempts: int = 1
    notes: Optional[str] = Field(default=None, description="Set only when no suggestion could be produced (e.g. VLM unreachable) — review the crop manually in that case.")


class TeachSymbolRequest(BaseModel):
    """
    Mirrors the Human-in-the-Loop popup fields exactly:
    Category / Name / Type / Tag format / Description / Table.
    Submitted as multipart form fields alongside the crop image — see
    POST /vlm/teach-symbol. The isa_equivalent/pip_equivalent/
    standard_reference/default_attributes fields are optional extras for
    callers carrying forward an AI TeachingSuggestion the human confirmed.
    """
    category: TeachingCategory
    name_type: str = Field(..., description="Name / Type — e.g. 'Special Ball Valve - Fire Safe'")
    tag_format: Optional[str] = Field(default=None, description="Tag format, if any — e.g. 'BV-XXXX'")
    description: str
    recommended_table: RecommendedTable
    standard_reference: str = "None"
    isa_equivalent: Optional[str] = None
    pip_equivalent: Optional[str] = None
    default_attributes: dict[str, Any] = Field(default_factory=dict)


class TeachSymbolResponse(BaseModel):
    symbol_dictionary_id: str
    category_name: str
    recommended_table: RecommendedTable
    added_to_dictionary: bool
    crop_saved_path: str
