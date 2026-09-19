"""Sumber RTSP tahan-putus untuk consumer IP camera."""
import os

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")  # sebelum import cv2

import logging
import threading
import time
from dataclasses import dataclass, field

import cv2

from aio_cctv.sources.base import Frame
from aio_cctv.sources.url_builder import mask

log = logging.getLogger(__name__)


@dataclass
class StreamStats:
    frames_received: int = 0
    frames_consumed: int = 0
    reconnects: int = 0
    last_frame_wall: float = 0.0
    connected_since: float | None = None
    downtime_s: float = 0.0
    recv_fps: float = 0.0
    _fps_window: list = field(default_factory=list)

    @property
    def dropped(self) -> int:           # frame yang dilewati karena konsumen lebih lambat
        return self.frames_received - self.frames_consumed


class RTSPSource:
    def __init__(self, url: str, name: str = "cam", open_timeout_ms: int = 5000,
                 read_timeout_ms: int = 5000, max_backoff_s: float = 5.0,
                 hw_accel: bool = False):
        self.url, self.name = url, name
        self.open_timeout_ms, self.read_timeout_ms = open_timeout_ms, read_timeout_ms
        self.max_backoff_s, self.hw_accel = max_backoff_s, hw_accel
        self.state = "idle"
        self.stats = StreamStats()
        self.fps, self.resolution = 0.0, (0, 0)
        self._latest: Frame | None = None
        self._last_consumed_seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._opened = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------- siklus hidup ----------
    def start(self, wait_s: float = 10.0) -> bool:
        self._thread = threading.Thread(target=self._run, name=f"rtsp-{self.name}", daemon=True)
        self._thread.start()
        return self._opened.wait(wait_s)       # True bila koneksi pertama berhasil

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.state = "stopped"

    @property
    def finished(self) -> bool:
        return self._stop.is_set()

    # ---------- konsumen ----------
    def read(self) -> Frame | None:
        with self._lock:
            f = self._latest
        if f is None or f.seq == self._last_consumed_seq:
            return None
        self._last_consumed_seq = f.seq
        self.stats.frames_consumed += 1
        return f

    # ---------- produsen (thread) ----------
    def _open(self) -> cv2.VideoCapture | None:
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.open_timeout_ms,
                  cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.read_timeout_ms]
        if self.hw_accel:
            params += [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY]
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)
        if not cap.isOpened():
            cap.release()
            return None
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        self.resolution = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                           int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        return cap

    def _run(self) -> None:
        backoff, seq, down_since = 1.0, 0, None
        while not self._stop.is_set():
            self.state = "connecting"
            log.info("[%s] membuka %s", self.name, mask(self.url))
            cap = self._open()
            if cap is None:
                self.state = "reconnecting"
                down_since = down_since or time.monotonic()
                log.warning("[%s] gagal terhubung, coba lagi %.0fs", self.name, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.max_backoff_s)
                continue

            if down_since is not None:
                self.stats.downtime_s += time.monotonic() - down_since
                down_since = None
            self.state, backoff = "streaming", 1.0
            self.stats.connected_since = time.time()
            self._opened.set()

            while not self._stop.is_set():
                ok, img = cap.read()
                if not ok or img is None:
                    break
                seq += 1
                now_m, now_w = time.monotonic(), time.time()
                self._update_fps(now_m)
                self.stats.frames_received += 1
                self.stats.last_frame_wall = now_w
                with self._lock:
                    self._latest = Frame(img, now_m, now_w, seq)
            cap.release()
            if not self._stop.is_set():
                self.state = "reconnecting"
                self.stats.reconnects += 1
                down_since = time.monotonic()
                log.warning("[%s] stream terputus, reconnect #%d", self.name, self.stats.reconnects)

    def _update_fps(self, now: float) -> None:
        w = self.stats._fps_window
        w.append(now)
        while w and now - w[0] > 2.0:
            w.pop(0)
        self.stats.recv_fps = len(w) / 2.0
