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
