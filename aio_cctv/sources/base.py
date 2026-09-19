from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class Frame:
    image: np.ndarray
    ts: float          # detik, monoton (dipakai untuk durasi)
    wall_ts: float     # epoch detik (dipakai untuk penyimpanan, Fase 6)
    seq: int


class FrameSource(Protocol):
    fps: float
    resolution: tuple[int, int]
    def start(self) -> None: ...
    def read(self) -> Frame | None: ...     # None = belum ada frame baru / selesai
    def stop(self) -> None: ...
    @property
    def finished(self) -> bool: ...
