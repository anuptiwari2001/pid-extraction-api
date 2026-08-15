"""
Stage 2: Symbol detection.

Wraps a YOLOv8 model (via `ultralytics`) fine-tuned on P&ID symbol classes.
IMPORTANT / HONEST LIMITATION: this module expects a real fine-tuned weights
file at settings.YOLO_MODEL_PATH. No such model ships with this repo — you
need to either train one (Roboflow's public P&ID symbol datasets are a
reasonable starting point) or point YOLO_MODEL_PATH at a model you already
have. Detectron2 support follows the same interface; see
`Detectron2SymbolDetector` below if you prefer that framework instead.

If no weights file is found, `SymbolDetector` falls back to a heuristic
contour-based detector (`HeuristicSymbolDetector`) so the pipeline still
runs end-to-end on vector-quality P&ID scans without a trained model — every
detection it makes is deliberately low-confidence and class "unknown" so it
routes straight into the human-in-the-loop flow rather than pretending to
know what it found. Swap in a trained model as soon as you have one; treat
the heuristic path as a bootstrap, not a long-term CV strategy.
"""
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.services.cv.symbol_signature import compute_signature

logger = get_logger(__name__)


@dataclass
class Detection:
    class_name: str
    confidence: float
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    shape_signature: dict = field(default_factory=dict)


class BaseSymbolDetector:
    def detect(self, image_path: str) -> list[Detection]:
        raise NotImplementedError


class YoloSymbolDetector(BaseSymbolDetector):
    """Real detector — requires `ultralytics` + a fine-tuned .pt weights file."""

    def __init__(self, model_path: str, device: str = "cpu"):
        from ultralytics import YOLO
        self.model = YOLO(model_path)
        self.device = device

    def detect(self, image_path: str) -> list[Detection]:
        results = self.model.predict(image_path, device=self.device, verbose=False)
        detections = []
        for r in results:
            names = r.names
            for box in r.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                detections.append(Detection(
                    class_name=names.get(cls_id, "unknown"),
                    confidence=conf,
                    bbox=(x1, y1, x2, y2),
                ))
        return detections


class Detectron2SymbolDetector(BaseSymbolDetector):
    """Alternative real detector for teams standardized on Detectron2 instead of YOLO."""

    def __init__(self, config_path: str, weights_path: str, device: str = "cpu"):
        from detectron2.engine import DefaultPredictor
        from detectron2.config import get_cfg

        cfg = get_cfg()
        cfg.merge_from_file(config_path)
        cfg.MODEL.WEIGHTS = weights_path
        cfg.MODEL.DEVICE = device
        self.predictor = DefaultPredictor(cfg)
        self.class_names: list[str] = []  # populate from your dataset metadata

    def detect(self, image_path: str) -> list[Detection]:
        import cv2
        image = cv2.imread(image_path)
        outputs = self.predictor(image)
        instances = outputs["instances"].to("cpu")
        detections = []
        for box, score, cls_id in zip(instances.pred_boxes, instances.scores, instances.pred_classes):
            x1, y1, x2, y2 = [float(v) for v in box]
            name = self.class_names[int(cls_id)] if int(cls_id) < len(self.class_names) else "unknown"
            detections.append(Detection(class_name=name, confidence=float(score), bbox=(x1, y1, x2, y2)))
        return detections


class HeuristicSymbolDetector(BaseSymbolDetector):
    """
    Bootstrap fallback used when no trained weights are configured. Finds
    closed contours of plausible symbol size and returns them as low-
    confidence "unknown" detections — every one will be routed to the
    human-in-the-loop endpoint rather than silently mis-classified.
    """

    def __init__(self, min_area: int = 400, max_area_ratio: float = 0.02):
        self.min_area = min_area
        self.max_area_ratio = max_area_ratio

    def detect(self, image_path: str) -> list[Detection]:
        import cv2

        image = cv2.imread(image_path)
        if image is None:
            return []
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        max_area = self.max_area_ratio * h * w

        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        detections = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            area = cw * ch
            if area < self.min_area or area > max_area:
                continue
            crop = gray[y:y + ch, x:x + cw]
            if crop.size == 0:
                continue
            sig = compute_signature(crop)
            detections.append(Detection(
                class_name="unknown",
                confidence=0.3,  # deliberately low — forces human-in-the-loop routing
                bbox=(float(x), float(y), float(x + cw), float(y + ch)),
                shape_signature=sig,
            ))
        logger.info("heuristic_detection_complete", extra={"context": {"count": len(detections), "image": image_path}})
        return detections


def get_symbol_detector() -> BaseSymbolDetector:
    settings = get_settings()
    if settings.YOLO_MODEL_PATH and os.path.exists(settings.YOLO_MODEL_PATH):
        try:
            return YoloSymbolDetector(settings.YOLO_MODEL_PATH, device=settings.CV_DEVICE)
        except Exception as exc:
            logger.warning("yolo_load_failed_falling_back", extra={"context": {"error": str(exc)}})
    logger.warning(
        "no_trained_symbol_model_found_using_heuristic_fallback",
        extra={"context": {"expected_path": settings.YOLO_MODEL_PATH}},
    )
    return HeuristicSymbolDetector()
