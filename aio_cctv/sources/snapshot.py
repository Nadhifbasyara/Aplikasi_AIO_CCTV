"""Ambil satu frame dari sumber mana pun — dipakai Tes Koneksi (Fase 5 §5.5)
dan 'Ambil snapshot' di editor zona (§5.6)."""
import time

import cv2
import numpy as np


def grab_frame(uri: str, timeout_s: float = 10.0) -> np.ndarray | None:
    if uri.startswith("rtsp://"):
        from aio_cctv.sources.rtsp_source import RTSPSource
        src = RTSPSource(uri, name="snapshot")
        try:
            if not src.start(wait_s=timeout_s):
                return None
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:     # frame pertama bisa telat 1-2 GOP
                if (f := src.read()) is not None:
                    return f.image
                time.sleep(0.05)
            return None
        finally:
            src.stop()

    cap = cv2.VideoCapture(int(uri) if uri.isdigit() else uri)   # angka = indeks webcam
    try:
        if not cap.isOpened():
            return None
        ok, img = cap.read()
        return img if ok else None
    finally:
        cap.release()
