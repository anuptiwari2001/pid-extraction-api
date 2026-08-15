"""
Process/instrument line detection via classical CV (Hough transform), used
to populate `lines` and to feed `connected_to` edges into relationship
inference. This is a geometry problem, not a learning problem — Hough line
detection on the (symbol-masked-out) page is a well-established, cheap, and
debuggable approach for P&ID line tracing and doesn't need training data.

Line TYPE (process vs. instrument signal vs. electrical) is distinguished by
stroke pattern (solid vs. dashed vs. dash-dot), matching ISA-5.1 line-type
conventions — this is a coarse approximation, not a certified line-type
classifier; treat `line_type` as a best-effort hint for review, not ground
truth for as-built documentation.
"""
import math
from dataclasses import dataclass, field

import numpy as np

from app.core.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TracedLine:
    path_points: list[tuple[float, float]]
    line_type: str  # process | pneumatic_signal | electrical_signal | unknown
    from_entity_id: str | None = None
    to_entity_id: str | None = None


def _classify_stroke(gray_strip: "np.ndarray") -> str:
    """Rough solid-vs-dashed check: fraction of dark pixels along the strip."""
    if gray_strip.size == 0:
        return "unknown"
    dark_fraction = float((gray_strip < 128).mean())
    if dark_fraction > 0.85:
        return "process"
    if 0.4 <= dark_fraction <= 0.85:
        return "pneumatic_signal"  # dashed
    return "electrical_signal"  # sparse/dash-dot approximation


def trace_lines(image_path: str, symbol_bboxes: list[tuple[float, float, float, float]],
                 endpoint_snap_radius: float = 20.0) -> list[TracedLine]:
    import cv2

    image = cv2.imread(image_path)
    if image is None:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Mask out symbol regions so Hough doesn't pick up symbol linework as pipe segments.
    mask = np.ones_like(gray) * 255
    for (x1, y1, x2, y2) in symbol_bboxes:
        mask[int(y1):int(y2), int(x1):int(x2)] = 0
    masked = cv2.bitwise_and(gray, mask)

    edges = cv2.Canny(masked, 50, 150)
    raw_lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=40, maxLineGap=8)

    traced: list[TracedLine] = []
    if raw_lines is None:
        return traced

    for line in raw_lines:
        x1, y1, x2, y2 = line[0]
        strip = gray[min(y1, y2):max(y1, y2) + 1, min(x1, x2):max(x1, x2) + 1]
        line_type = _classify_stroke(strip)
        traced.append(TracedLine(path_points=[(float(x1), float(y1)), (float(x2), float(y2))], line_type=line_type))

    logger.info("lines_traced", extra={"context": {"count": len(traced), "image": image_path}})
    return traced


def snap_endpoints_to_entities(
    lines: list[TracedLine],
    entities: list,  # list of Entity from rule_based_relations, each with .id and .bbox
    snap_radius: float = 25.0,
) -> list[TracedLine]:
    def _center(bbox):
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2, (y1 + y2) / 2

    def _nearest_entity(point):
        best, best_d = None, float("inf")
        for e in entities:
            ex, ey = _center(e.bbox)
            d = math.hypot(point[0] - ex, point[1] - ey)
            if d < best_d:
                best, best_d = e, d
        return (best, best_d)

    for line in lines:
        start, end = line.path_points[0], line.path_points[-1]
        e_start, d_start = _nearest_entity(start)
        e_end, d_end = _nearest_entity(end)
        if e_start and d_start <= snap_radius:
            line.from_entity_id = e_start.id
        if e_end and d_end <= snap_radius:
            line.to_entity_id = e_end.id

    return lines
