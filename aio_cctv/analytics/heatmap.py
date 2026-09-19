import cv2
import numpy as np
import supervision as sv


class HeatmapAccumulator:
    """Histogram 2D titik kaki. cell = ukuran sel (px) -> hemat memori & cepat."""

    def __init__(self, w: int, h: int, cell: int = 8, decay: float = 1.0):
        self.w, self.h, self.cell, self.decay = w, h, cell, decay
        self.acc = np.zeros((h // cell + 1, w // cell + 1), dtype=np.float32)

    def update(self, det: sv.Detections) -> None:
        if self.decay < 1.0:
            self.acc *= self.decay           # heatmap "bergerak" untuk mode live
        if not len(det):
            return
        pts = det.get_anchors_coordinates(sv.Position.BOTTOM_CENTER)
        xs = np.clip((pts[:, 0] // self.cell).astype(int), 0, self.acc.shape[1] - 1)
        ys = np.clip((pts[:, 1] // self.cell).astype(int), 0, self.acc.shape[0] - 1)
        np.add.at(self.acc, (ys, xs), 1.0)   # AKUMULASI (perbaikan dari §8.2)

    def render(self, background: np.ndarray | None = None, alpha: float = 0.55) -> np.ndarray:
        hm = cv2.resize(self.acc, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
        hm = cv2.GaussianBlur(hm, (0, 0), sigmaX=self.cell * 3)
        hm = cv2.normalize(hm, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        color = cv2.applyColorMap(hm, cv2.COLORMAP_JET)
        if background is None:
            return color
        return cv2.addWeighted(color, alpha, background, 1 - alpha, 0)
