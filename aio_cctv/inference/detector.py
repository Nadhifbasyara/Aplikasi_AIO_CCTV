"""Pembungkus detektor objek. Backend default: Ultralytics YOLOv11 (Proposal §7)."""
from __future__ import annotations

import numpy as np
import supervision as sv
from ultralytics import YOLO

COCO_NAME_TO_ID = {"person": 0}   # diperluas bila target_classes bertambah (Proposal §5)


class Detector:
    def __init__(self, weights: str = "models/yolo11n.pt", classes: list[str] | None = None,
                 conf: float = 0.35, iou: float = 0.5, imgsz: int = 640, device: str | None = None):
        self.model = YOLO(weights)
        self.class_ids = [COCO_NAME_TO_ID[c] for c in (classes or ["person"])]
        self.conf, self.iou, self.imgsz, self.device = conf, iou, imgsz, device

    def __call__(self, frame: np.ndarray) -> sv.Detections:
        result = self.model(frame, classes=self.class_ids, conf=self.conf, iou=self.iou,
                            imgsz=self.imgsz, device=self.device, verbose=False)[0]
        return sv.Detections.from_ultralytics(result)

    def warmup(self) -> None:
        self(np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8))
