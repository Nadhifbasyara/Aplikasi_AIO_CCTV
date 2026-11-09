"""Pembungkus detektor YOLO (Ultralytics). Fase 6: bobot & kelas datang dari paket modul."""
from __future__ import annotations

import numpy as np
import supervision as sv
from ultralytics import YOLO


class Detector:
    def __init__(self, weights: str = "models/yolo11n.pt", classes: list[str] | None = None,
                 conf: float = 0.35, iou: float = 0.5, imgsz: int = 640, device: str | None = None):
        """classes: nama kelas dari `model.names` bobot itu sendiri; kosong/None = semua kelas.
        Bobot .pt, .onnx, .engine, atau folder OpenVINO dimuat dengan cara yang sama."""
        self.weights = str(weights)
        self.model = YOLO(self.weights)
        self.names: dict[int, str] = dict(self.model.names)
        self.class_ids = self._resolve(classes)
        self.conf, self.iou, self.imgsz, self.device = conf, iou, imgsz, device

    @classmethod
    def from_manifest(cls, manifest, device: str | None = None) -> Detector:
        spec = manifest.model
        return cls(str(manifest.weights_path), spec.classes, spec.conf, spec.iou, spec.imgsz,
                   device)

    def _resolve(self, classes: list[str] | None) -> list[int] | None:
        if not classes:
            return None
        by_name = {n: i for i, n in self.names.items()}
        if missing := [c for c in classes if c not in by_name]:
            raise ValueError(f"Kelas {missing} tidak ada di model {self.weights}; "
                             f"tersedia: {sorted(by_name)}")
        return [by_name[c] for c in classes]

    def _kwargs(self) -> dict:
        return {"classes": self.class_ids, "conf": self.conf, "iou": self.iou,
                "imgsz": self.imgsz, "device": self.device, "verbose": False}

    def __call__(self, frame: np.ndarray) -> sv.Detections:
        return sv.Detections.from_ultralytics(self.model(frame, **self._kwargs())[0])

    def detect_batch(self, frames: list[np.ndarray]) -> list[sv.Detections]:
        """Dipakai InferenceServer (Fase 9)."""
        return [sv.Detections.from_ultralytics(r) for r in self.model(frames, **self._kwargs())]

    def warmup(self) -> None:
        self(np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8))
