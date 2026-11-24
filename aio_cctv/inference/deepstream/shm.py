"""Saluran shared memory satu kamera antara runner DeepStream (container) dan aplikasi (host).

Dipakai di DUA sisi, jadi hanya bergantung pada numpy + stdlib dan kompatibel Python 3.11
(venv aplikasi) maupun 3.12 (container DeepStream). File berada di /dev/shm; container
dijalankan dengan `--ipc=host` sehingga melihat file yang sama.

Tata letak (little-endian):
    [0,    256)   header  (lihat HEADER_DTYPE)
    [256,  256 + MAX_DET*24)   deteksi float32 [x1, y1, x2, y2, conf, class_id]
    [FRAME_OFF, FRAME_OFF + w*h*3)   frame BGR uint8 (resolusi nvstreammux)

Protokol "frame atas permintaan" (Fase 9 §5.8, baris B2): frame hanya disalin dari GPU bila
aplikasi memintanya, jadi FPS target & kamera yang tidak dianalisis tidak membebani PCIe/CPU.
    host   : state IDLE  -> WANT        (minta frame berikutnya)
    runner : state WANT  -> (tulis frame + deteksi + header) -> READY
    host   : state READY -> (salin) -> IDLE
Setiap sisi hanya menulis transisi miliknya, sehingga tidak perlu lock lintas proses.
"""
from __future__ import annotations

import mmap
import os
from pathlib import Path

import numpy as np

MAGIC = 0x41494F44              # "AIOD"
VERSION = 2
MAX_DET = 512
DET_OFF = 256
FRAME_OFF = 16384               # ≥ DET_OFF + MAX_DET*24, rata ke halaman
SHM_DIR = Path("/dev/shm")

IDLE, WANT, READY = 0, 1, 2
# status koneksi sumber (diisi runner)
CONNECTING, STREAMING, RECONNECTING, STOPPED = 0, 1, 2, 3
STATE_NAMES = {CONNECTING: "connecting", STREAMING: "streaming",
               RECONNECTING: "reconnecting", STOPPED: "stopped"}

HEADER_DTYPE = np.dtype([
    ("magic", "<u4"), ("version", "<u4"),
    ("state", "<u4"),            # IDLE/WANT/READY
    ("conn", "<u4"),             # CONNECTING/STREAMING/...
    ("width", "<u4"), ("height", "<u4"),          # ukuran frame di slot
    ("src_width", "<u4"), ("src_height", "<u4"),  # resolusi asli kamera
    ("seq", "<u8"),              # nomor frame yang diserahkan ke host
    ("frames_total", "<u8"),     # frame dari kamera yang selesai di-decode (NVDEC), di mux
    ("frames_sink", "<u8"),      # frame yang sampai ke appsink (sisanya hilang karena beban)
    ("ts", "<f8"),               # time.monotonic() saat frame keluar dari decoder/mux
    ("wall", "<f8"),             # time.time() pada saat yang sama
    ("n_det", "<u4"), ("reconnects", "<u4"),
    ("fps", "<f4"),              # FPS kamera (frame ter-decode per detik, diukur di mux)
    ("infer_ms", "<f4"),         # mux → keluar nvinfer, rata-rata bergerak (per batch)
    ("copy_ms", "<f4"),          # salin GPU → shm untuk frame ini
    ("heartbeat", "<f8"),        # time.monotonic() terakhir runner hidup
])
assert HEADER_DTYPE.itemsize <= DET_OFF


def slot_size(width: int, height: int) -> int:
    return FRAME_OFF + width * height * 3


def slot_path(name: str) -> Path:
    return SHM_DIR / name


class Slot:
    """Pemetaan satu file shm. `create=True` hanya oleh host (pemilik, yang juga menghapus)."""

    def __init__(self, path: str | os.PathLike, width: int = 0, height: int = 0,
                 create: bool = False):
        self.path = Path(path)
        if create:
            size = slot_size(width, height)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600)
            try:
                os.ftruncate(fd, size)
                self._mm = mmap.mmap(fd, size)
            finally:
                os.close(fd)
            self.header = np.ndarray((), HEADER_DTYPE, self._mm, 0)
            self.header["magic"], self.header["version"] = MAGIC, VERSION
            self.header["width"], self.header["height"] = width, height
        else:
            fd = os.open(self.path, os.O_RDWR)
            try:
                self._mm = mmap.mmap(fd, 0)
            finally:
                os.close(fd)
            self.header = np.ndarray((), HEADER_DTYPE, self._mm, 0)
            if int(self.header["magic"]) != MAGIC or int(self.header["version"]) != VERSION:
                raise ValueError(f"slot {self.path} bukan format AIO v{VERSION}")
        w, h = int(self.header["width"]), int(self.header["height"])
        self.dets = np.ndarray((MAX_DET, 6), np.float32, self._mm, DET_OFF)
        self.frame = np.ndarray((h, w, 3), np.uint8, self._mm, FRAME_OFF)

    # ------------------------------------------------------------ akses praktis
    def get(self, key: str):
        return self.header[key].item()

    def set(self, key: str, value) -> None:
        self.header[key] = value

    def close(self) -> None:
        # view numpy harus dilepas dulu, kalau tidak mmap.close() menolak (BufferError)
        self.header = self.dets = self.frame = None
        try:
            self._mm.close()
        except BufferError:
            pass

    def unlink(self) -> None:
        self.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
