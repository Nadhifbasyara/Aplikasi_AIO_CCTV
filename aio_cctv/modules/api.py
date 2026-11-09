"""Kontrak modul analitik AIO-CCTV v1 (Fase 6 §4.3).

File ini boleh disalin apa adanya ke repo pembuat modul (TA-1/TA-2): hanya bergantung
pada numpy dan supervision.

Host memanggil satu instans modul per kamera:
    m = Kelas(ctx)                        # sekali, saat kamera mulai
    r = m.process(frame, detections, ts)  # tiap frame yang diproses
    m.close(ts)                           # sekali, saat kamera berhenti
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import supervision as sv

API_VERSION = "1.0"


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    kind: str                               # "zone" | "line"
    points: list[tuple[float, float]]       # ternormalisasi 0..1; garis = [p1, p2] (arah IN =
                                            # konvensi sv.LineZone, panah di editor zona)
    role: str | None = None                 # peran dari manifest.regions, mis. "meja_kerja"


@dataclass(frozen=True)
class CameraContext:
    camera_id: str
    frame_wh: tuple[int, int]               # resolusi frame yang akan diterima
    fps: float                              # FPS EFEKTIF yang diproses (sesudah stride, Fase 9)
    regions: list[Region]
    params: dict                            # sudah digabung dengan default manifest
    module_dir: str                         # path paket, untuk memuat bobot Re-ID dsb.

    def zones(self) -> list[Region]:
        return [r for r in self.regions if r.kind == "zone"]

    def lines(self) -> list[Region]:
        return [r for r in self.regions if r.kind == "line"]

    def to_pixels(self, points: list[tuple[float, float]]) -> np.ndarray:
        """Titik ternormalisasi -> piksel int32 pada resolusi kamera ini (konvensi Fase 1)."""
        w, h = self.frame_wh
        return np.array([[round(x * (w - 1)), round(y * (h - 1))] for x, y in points],
                        dtype=np.int32)


@dataclass
class Metric:
    key: str                                # harus terdaftar di manifest.metrics
    value: float
    region_id: str | None = None


@dataclass
class Event:
    type: str                               # harus terdaftar di manifest.events
    ts: float
    region_id: str | None = None
    track_id: int | None = None
    data: dict = field(default_factory=dict)   # JSON sederhana, maks 2 KB; DILARANG berisi citra


@dataclass
class ModuleResult:
    tracks: sv.Detections                   # deteksi + tracker_id
    metrics: list[Metric] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    labels: dict[int, str] = field(default_factory=dict)  # track_id -> teks overlay
    heat: np.ndarray | None = None          # opsional: grid akumulasi ≥ 0; host yang menormalisasi


class AnalyticsModule:
    """Kelas dasar. Modul cukup meng-override process() dan bila perlu close()."""

    def __init__(self, ctx: CameraContext) -> None:
        self.ctx = ctx

    def process(self, frame: np.ndarray, detections: sv.Detections, ts: float) -> ModuleResult:
        """frame: BGR uint8 read-only. detections: hasil YOLO host, sudah difilter kelas & conf.
        ts: detik monotonic dari source. Harus cepat; jangan blocking I/O."""
        raise NotImplementedError

    def close(self, ts: float) -> list[Event]:
        """Tutup sesi yang masih terbuka (mis. dwell/idle berjalan) saat kamera berhenti."""
        return []
