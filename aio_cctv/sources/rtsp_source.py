"""Sumber RTSP tahan-putus untuk consumer IP camera."""
import os

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")  # sebelum import cv2

import logging
import random
import threading
import time
from dataclasses import dataclass, field

import cv2

try:
    import av
    _HAS_AV = True
except ImportError:
    _HAS_AV = False

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
    bytes_received: int = 0
    bandwidth_kbps: float = 0.0
    decode_ms: float = 0.0             # rata-rata bergerak decode per frame (thread penerima)
    convert_ms: float = 0.0            # konversi ke BGR, hanya frame yang dipakai (thread worker)
    _fps_window: list = field(default_factory=list)
    _bw_window: list = field(default_factory=list)

    @property
    def dropped(self) -> int:
        return self.frames_received - self.frames_consumed

    @property
    def dropped_rate(self) -> float:
        if self.frames_received == 0:
            return 0.0
        return self.dropped / self.frames_received * 100.0


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
        self._latest_av = None             # (VideoFrame, ts, wall_ts, seq) belum dikonversi
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
            f, raw = self._latest, self._latest_av
        if raw is not None:                    # PyAV: konversi BGR baru dilakukan di sini, jadi
            av_frame, ts, wall, seq = raw      # frame yang tertimpa sebelum dibaca tidak pernah
            if seq == self._last_consumed_seq:  # dikonversi (Fase 9 §5.3)
                return None
            t0 = time.perf_counter()
            f = Frame(av_frame.to_ndarray(format="bgr24"), ts, wall, seq)
            self.stats.convert_ms += 0.1 * ((time.perf_counter() - t0) * 1e3
                                            - self.stats.convert_ms)
        if f is None or f.seq == self._last_consumed_seq:
            return None
        self._last_consumed_seq = f.seq
        self.stats.frames_consumed += 1
        return f

    def has_new(self) -> bool:
        """Ada frame yang belum dibaca (tanpa mengonsumsinya)."""
        with self._lock:
            seq = self._latest_av[3] if self._latest_av is not None else (
                self._latest.seq if self._latest is not None else 0)
        return seq != 0 and seq != self._last_consumed_seq

    # ---------- produsen (thread) ----------
    def _open_cv(self) -> cv2.VideoCapture | None:
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
        if _HAS_AV:
            self._run_av()
        else:
            log.info("[%s] PyAV tidak tersedia, fallback ke OpenCV (bandwidth = uncompressed)", self.name)
            self._run_cv()

    def _run_av(self) -> None:
        backoff, seq, down_since = 1.0, 0, None
        while not self._stop.is_set():
            self.state = "connecting"
            log.info("[%s] membuka (PyAV) %s", self.name, mask(self.url))
            container = None
            try:
                options = {"rtsp_transport": "tcp", "stimeout": str(self.open_timeout_ms * 1000)}
                container = av.open(self.url, options=options, timeout=(self.open_timeout_ms / 1000.0, self.read_timeout_ms / 1000.0))
                video_stream = container.streams.video[0]
                video_stream.thread_type = "AUTO"
                self.fps = float(video_stream.average_rate or video_stream.rate or 15)
                self.resolution = (video_stream.codec_context.width,
                                   video_stream.codec_context.height)
            except Exception as exc:
                self.state = "reconnecting"
                down_since = down_since or time.monotonic()
                # pesan error PyAV memuat URL lengkap termasuk password: wajib disamarkan
                log.warning("[%s] gagal terhubung (PyAV): %s, coba lagi %.0fs", self.name,
                            mask(str(exc)), backoff)
                if container is not None:
                    container.close()
                self._stop.wait(backoff * random.uniform(0.75, 1.25))   # jitter: channel DVR tidak serentak
                backoff = min(backoff * 2, self.max_backoff_s)
                continue

            if down_since is not None:
                self.stats.downtime_s += time.monotonic() - down_since
                down_since = None
            self.state, backoff = "streaming", 1.0
            self.stats.connected_since = time.time()
            self._opened.set()

            try:
                for packet in container.demux(video_stream):
                    if self._stop.is_set():
                        break
                    compressed_bytes = packet.size if packet.size else 0
                    t_dec = time.perf_counter()
                    frames = packet.decode()
                    for av_frame in frames:
                        seq += 1
                        now_m, now_w = time.monotonic(), time.time()
                        dec = (time.perf_counter() - t_dec) * 1e3
                        self.stats.decode_ms += 0.1 * (dec - self.stats.decode_ms)
                        t_dec = time.perf_counter()
                        self.stats.bytes_received += compressed_bytes
                        self._update_fps(now_m, compressed_bytes)
                        self.stats.frames_received += 1
                        self.stats.last_frame_wall = now_w
                        with self._lock:
                            self._latest_av = (av_frame, now_m, now_w, seq)
                        compressed_bytes = 0
            except Exception as exc:
                if not self._stop.is_set():
                    log.warning("[%s] stream error (PyAV): %s", self.name, mask(str(exc)))
            finally:
                container.close()

            if not self._stop.is_set():
                self.state = "reconnecting"
                self.stats.reconnects += 1
                down_since = time.monotonic()
                log.warning("[%s] stream terputus, reconnect #%d", self.name, self.stats.reconnects)

    def _run_cv(self) -> None:
        backoff, seq, down_since = 1.0, 0, None
        while not self._stop.is_set():
            self.state = "connecting"
            log.info("[%s] membuka %s", self.name, mask(self.url))
            cap = self._open_cv()
            if cap is None:
                self.state = "reconnecting"
                down_since = down_since or time.monotonic()
                log.warning("[%s] gagal terhubung, coba lagi %.0fs", self.name, backoff)
                self._stop.wait(backoff * random.uniform(0.75, 1.25))   # jitter: channel DVR tidak serentak
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
                frame_bytes = img.nbytes
                self.stats.bytes_received += frame_bytes
                self._update_fps(now_m, frame_bytes)
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

    def _update_fps(self, now: float, frame_bytes: int = 0) -> None:
        w = self.stats._fps_window
        w.append(now)
        while w and now - w[0] > 2.0:
            w.pop(0)
        self.stats.recv_fps = len(w) / 2.0

        bw = self.stats._bw_window
        bw.append((now, frame_bytes))
        while bw and now - bw[0][0] > 2.0:
            bw.pop(0)
        total_bytes = sum(b for _, b in bw)
        self.stats.bandwidth_kbps = total_bytes / 2.0 / 1024.0
