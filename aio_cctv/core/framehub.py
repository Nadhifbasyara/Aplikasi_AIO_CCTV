"""Frame terakhir per kamera, aman dibaca dari thread lain (Fase 8).

Worker menitipkan frame pada laju tampilan GUI yang sudah ada (display_fps), jadi tidak
ada anotasi tambahan. Pembacanya: snapshot/MJPEG dashboard web dan snapshot ekspor profil.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class HubFrame:
    annotated: np.ndarray               # sudah berisi overlay (dan blur bila mode anonim aktif)
    raw: np.ndarray                     # tanpa overlay
    blurred: bool
    ts: float                           # time.monotonic() saat dititipkan
    seq: int


class FrameHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._frames: dict[str, HubFrame] = {}
        self._seq = 0
        self._demand: dict[str, float] = {}     # kapan terakhir frame kamera ini diminta

    def put(self, cam_id: str, annotated: np.ndarray, raw: np.ndarray, blurred: bool) -> None:
        with self._lock:
            self._seq += 1
            self._frames[cam_id] = HubFrame(annotated, raw, blurred, time.monotonic(), self._seq)

    def get(self, cam_id: str) -> HubFrame | None:
        with self._lock:
            self._demand[cam_id] = time.monotonic()
            return self._frames.get(cam_id)

    def wanted(self, cam_id: str, within_s: float = 5.0) -> bool:
        """Perlu dititipi frame? Ya bila diminta (web, thumbnail) dalam `within_s` detik terakhir,
        atau belum punya frame sama sekali. Dipakai worker untuk render hemat (Fase 9 §5.5)."""
        with self._lock:
            if cam_id not in self._frames:
                return True
            return time.monotonic() - self._demand.get(cam_id, -1e9) < within_s

    def drop(self, cam_id: str) -> None:
        with self._lock:
            self._frames.pop(cam_id, None)
            self._demand.pop(cam_id, None)

    def jpeg(self, cam_id: str, max_width: int = 960, quality: int = 75,
             annotated: bool = True) -> tuple[bytes, int] | None:
        """(JPEG, seq) frame terakhir, diperkecil bila lebih lebar dari max_width."""
        f = self.get(cam_id)
        if f is None:
            return None
        img = f.annotated if annotated or f.blurred else f.raw   # blur tidak boleh dilewati
        h, w = img.shape[:2]
        if w > max_width:
            img = cv2.resize(img, (max_width, round(h * max_width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return (buf.tobytes(), f.seq) if ok else None
