"""Sumber kamera lewat Jalur B (DeepStream, Fase 9 §5.8).

Antarmukanya sama dengan RTSPSource (start/read/stop, state, stats, fps, resolution), tetapi
decode (NVDEC) dan deteksi (TensorRT FP16) sudah dikerjakan container DeepStream bersama
kamera lain. `read()` mengembalikan Frame yang membawa `detections`, sehingga Pipeline
melewati detektor Python dan langsung ke modul (tracker + analitik rekan).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import supervision as sv

from aio_cctv.inference.deepstream import shm
from aio_cctv.sources.base import Frame


@dataclass
class DSStats:
    """Nama kolom sama dengan StreamStats RTSPSource (dibaca PipelineWorker & benchmark)."""
    frames_received: int = 0
    frames_consumed: int = 0
    frames_lost: int = 0                 # ter-decode tetapi tidak sampai appsink (beban GPU)
    reconnects: int = 0
    recv_fps: float = 0.0
    bandwidth_kbps: float | None = None  # tidak terukur: paket RTSP ditangani nvurisrcbin
    downtime_s: float = 0.0
    decode_ms: float = 0.0               # NVDEC di GPU, tidak membebani CPU host
    convert_ms: float = 0.0              # salin frame GPU → shm (di runner)

    @property
    def dropped_rate(self) -> float:
        if self.frames_received == 0:
            return 0.0
        return (self.frames_received - self.frames_consumed) / self.frames_received * 100.0


class DeepStreamSource:
    def __init__(self, url: str, name: str, manifest, key: tuple, width: int = 1280,
                 height: int = 720, pool=None, start_timeout_s: float = 120.0):
        """key: kunci grup model (lihat pipeline.detector_key). pool: untuk pengujian."""
        if pool is None:
            from aio_cctv.inference.deepstream.service import POOL as pool
        self.url, self.name, self.manifest, self.key = url, name, manifest, key
        self.width, self.height, self.pool = width, height, pool
        self.start_timeout_s = start_timeout_s
        self.cancel = None                       # diisi worker: berhenti menunggu bila dimatikan
        self.stats = DSStats()
        self.fps, self.resolution = 0.0, (width, height)    # resolution = frame dianalisis
        self.source_resolution = (0, 0)                     # resolusi asli kamera
        self.names: list[str] = []
        self.slot: shm.Slot | None = None
        self.group = None
        self._stopped = False
        self._last_seq = 0
        self._down_since: float | None = None
        self.connected_since: float | None = None            # epoch, untuk uptime

    # ---------- siklus hidup ----------
    def start(self) -> bool:
        from aio_cctv.inference.deepstream.service import wait_streaming
        self.group, self.slot = self.pool.acquire(self.key, self.manifest, self.name, self.url,
                                                  self.width, self.height)
        self.names = list(getattr(self.group, "names", []))
        ok = wait_streaming(self.slot, self.start_timeout_s, self.cancel)
        self._refresh()
        return ok

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            self.pool.release(self.name)
            self.slot = None

    @property
    def finished(self) -> bool:
        return self._stopped

    @property
    def state(self) -> str:
        if self.slot is None:
            return "stopped"
        return shm.STATE_NAMES.get(self.slot.get("conn"), "connecting")

    @property
    def error(self) -> str:
        """Alasan DeepStream gagal/berhenti (VRAM habis, stream tak terbuka, …); "" = normal."""
        g = self.group
        if g is None:
            return ""
        if getattr(g, "last_error", ""):
            return g.last_error
        if self.state != "streaming" and hasattr(g, "log_tail"):
            from aio_cctv.inference.deepstream.service import diagnose
            return diagnose(g.log_tail(20000))
        return ""

    @property
    def infer_ms(self) -> float:
        return float(self.slot.get("infer_ms")) if self.slot is not None else 0.0

    def _refresh(self) -> None:
        s = self.slot
        if s is None:
            return
        self.stats.frames_received = int(s.get("frames_total"))
        self.stats.frames_lost = max(0, self.stats.frames_received - int(s.get("frames_sink"))
                                     - 8)              # ±8 frame selalu sedang di pipeline
        self.stats.recv_fps = float(s.get("fps"))
        if s.get("src_width"):
            self.source_resolution = (int(s.get("src_width")), int(s.get("src_height")))
        self.stats.reconnects = int(s.get("reconnects"))
        self.stats.convert_ms = float(s.get("copy_ms"))
        if self.stats.recv_fps > 0:
            self.fps = self.stats.recv_fps
        now = time.monotonic()
        if self.state == "streaming":
            if self.connected_since is None:
                self.connected_since = time.time()
            if self._down_since is not None:
                self.stats.downtime_s += now - self._down_since
                self._down_since = None
        else:
            self.connected_since = None
            if self._down_since is None and self.stats.frames_received:
                self._down_since = now

    # ---------- konsumen ----------
    def read(self) -> Frame | None:
        s = self.slot
        if s is None:
            return None
        self._refresh()
        st = s.get("state")
        if st == shm.IDLE:                      # minta frame berikutnya; disalin runner
            s.set("state", shm.WANT)
            return None
        if st != shm.READY:
            return None
        seq = int(s.get("seq"))
        n = int(s.get("n_det"))
        dets = s.dets[:n].copy()
        image = s.frame.copy()
        ts, wall = float(s.get("ts")), float(s.get("wall"))
        s.set("state", shm.IDLE)                # slot boleh ditimpa lagi
        if seq == self._last_seq:
            return None
        self._last_seq = seq
        self.stats.frames_consumed += 1
        f = Frame(image, ts, wall, seq)
        f.detections = self._detections(dets)
        return f

    def has_new(self) -> bool:
        return self.slot is not None and self.slot.get("state") == shm.READY

    def _detections(self, dets: np.ndarray) -> sv.Detections:
        if not len(dets):
            return sv.Detections.empty()
        class_id = dets[:, 5].astype(int)
        names = self.names
        data = {"class_name": np.array([names[c] if 0 <= c < len(names) else str(c)
                                        for c in class_id])}
        return sv.Detections(xyxy=dets[:, :4].astype(np.float32),
                             confidence=dets[:, 4].astype(np.float32),
                             class_id=class_id, data=data)
