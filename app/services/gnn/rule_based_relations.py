"""
Stage 4 (primary path): rule-based relationship inference.

Why rule-based is the primary path and the GNN is an optional refinement:
P&ID connectivity is governed by drafting conventions (ISA-5.1 line-type
styles, arrowheads, tag proximity to symbols, instrument bubbles tethered to
their measured/controlled equipment) that are directly encodable as rules,
and there is no labeled connectivity dataset for a GNN to train on yet. The
human-in-the-loop labels collected over time (see UnknownSymbol / symbol
dictionary) are exactly the kind of data you'd eventually use to train a
GNN — see gnn_model.py for that path once you have enough of it.

Relation types produced (matches the spec): connected_to, controls,
measures, belongs_to.
"""
import math
from dataclasses import dataclass

from app.core.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Entity:
    id: str
    entity_type: str  # symbol|instrument|equipment|line
    class_name: str
    bbox: tuple[float, float, float, float]
    tag: str | None = None


def _center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2, (y1 + y2) / 2


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


INSTRUMENT_FUNCTION_HINTS = {
    # first ISA-5.1 letter -> relation the instrument has to what it's tagged on
    "F": "measures", "P": "measures", "T": "measures", "L": "measures",
    "A": "measures",  # analyzer
    "C": "controls",  # e.g. FIC, TIC controlling a valve/final element
    "S": "controls",  # switch
    "V": "controls",  # control valve acting on a line
}


def infer_relationships(
    entities: list[Entity],
    lines: list[dict],  # [{id, from_point, to_point, path_points}]
    proximity_threshold_px: float = 150.0,
) -> list[dict]:
    """
    Returns a list of {source_entity_id, source_entity_type, target_entity_id,
    target_entity_type, relation_type, confidence, inferred_by}.
    """
    relationships: list[dict] = []

    # 1) belongs_to: an instrument symbol whose tag was read near an
    #    equipment symbol is assumed to belong to / be mounted on it.
    equipment_entities = [e for e in entities if e.entity_type == "equipment"]
    instrument_entities = [e for e in entities if e.entity_type == "instrument"]

    for inst in instrument_entities:
        inst_center = _center(inst.bbox)
        nearest_eq, nearest_dist = None, float("inf")
        for eq in equipment_entities:
            d = _distance(inst_center, _center(eq.bbox))
            if d < nearest_dist:
                nearest_eq, nearest_dist = eq, d
        if nearest_eq and nearest_dist <= proximity_threshold_px:
            relationships.append({
                "source_entity_id": inst.id, "source_entity_type": "instrument",
                "target_entity_id": nearest_eq.id, "target_entity_type": "equipment",
                "relation_type": "belongs_to",
                "confidence": max(0.4, 1.0 - nearest_dist / (proximity_threshold_px * 2)),
                "inferred_by": "rule_based",
            })

        # 2) controls / measures: derive from the instrument's ISA function
        #    letter (first letter of its tag, e.g. "F" in FIC-101).
        if inst.tag:
            first_letter = inst.tag[0].upper()
            relation = INSTRUMENT_FUNCTION_HINTS.get(first_letter)
            if relation and nearest_eq:
                relationships.append({
                    "source_entity_id": inst.id, "source_entity_type": "instrument",
                    "target_entity_id": nearest_eq.id, "target_entity_type": "equipment",
                    "relation_type": relation,
                    "confidence": 0.6,
                    "inferred_by": "rule_based",
                })

    # 3) connected_to: lines whose endpoints fall within tolerance of an
    #    entity's bounding box are connected to that entity. This assumes
    #    `lines` already carry resolved from/to points from line tracing
    #    (polyline endpoint detection) — see extraction_orchestrator.
    entity_by_id = {e.id: e for e in entities}
    for line in lines:
        from_entity_id = line.get("from_entity_id")
        to_entity_id = line.get("to_entity_id")
        if from_entity_id and to_entity_id and from_entity_id in entity_by_id and to_entity_id in entity_by_id:
            src, tgt = entity_by_id[from_entity_id], entity_by_id[to_entity_id]
            relationships.append({
                "source_entity_id": src.id, "source_entity_type": src.entity_type,
                "target_entity_id": tgt.id, "target_entity_type": tgt.entity_type,
                "relation_type": "connected_to",
                "confidence": 0.7,
                "inferred_by": "rule_based",
            })

    logger.info("relationships_inferred", extra={"context": {"count": len(relationships)}})
    return relationships
