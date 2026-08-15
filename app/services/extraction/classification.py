"""
Maps a raw (symbol detection + nearby OCR text) pair onto the domain model:
is this an instrument bubble, a piece of equipment, or something else? This
is intentionally simple keyword/pattern matching rather than a separate ML
classifier — the CV model's `class_name` (once trained) already carries this
distinction; this module exists mostly for the heuristic-fallback detector
and for deriving ISA-5.1 attributes from the matched tag text.
"""
import re

# Coarse equipment-type keyword table — extend as your symbol taxonomy grows.
EQUIPMENT_KEYWORDS = {
    "pump": "pump", "compressor": "compressor", "vessel": "vessel",
    "tank": "tank", "exchanger": "heat_exchanger", "column": "column",
    "reactor": "reactor", "valve": "valve", "filter": "filter",
    "separator": "separator", "drum": "drum",
}

EQUIPMENT_TAG_PREFIX_PATTERN = re.compile(r"^([A-Z]{1,2})-?(\d{3,4})$")  # e.g. P-101, V-201, E-301


def classify_symbol(class_name: str, nearby_text: str | None) -> dict:
    """
    Returns {"category": "instrument"|"equipment"|"annotation"|"unknown", ...attrs}
    """
    class_lower = (class_name or "").lower()

    for keyword, eq_type in EQUIPMENT_KEYWORDS.items():
        if keyword in class_lower:
            return {"category": "equipment", "equipment_type": eq_type}

    if nearby_text:
        text_upper = nearby_text.upper().strip()
        # Instrument bubble: ISA-5.1 tag with a recognized function letter
        from app.services.ocr.text_extractor import parse_isa_tag
        isa = parse_isa_tag(text_upper)
        if isa:
            return {"category": "instrument", **isa}

        eq_match = EQUIPMENT_TAG_PREFIX_PATTERN.match(text_upper.replace(" ", ""))
        if eq_match:
            return {"category": "equipment", "tag": text_upper}

    if class_name == "unknown":
        return {"category": "unknown"}

    return {"category": "annotation"}
