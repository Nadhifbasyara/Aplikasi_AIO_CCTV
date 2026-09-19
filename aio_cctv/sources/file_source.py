import time

import cv2

from aio_cctv.sources.base import Frame


class FileSource:
    """Sumber file video. ts = posisi waktu video, sehingga hasil deterministik & bisa diulang."""

    def __init__(self, path: str):
        self.path = path
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise IOError(f"Tidak bisa membuka {path}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.resolution = (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                           int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        self._seq = 0
        self._finished = False
        self._t0_wall = time.time()

    def start(self) -> None:
        pass

    def read(self) -> Frame | None:
        ok, img = self.cap.read()
        if not ok:
            self._finished = True
            return None
        self._seq += 1
        ts = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 or self._seq / self.fps
        return Frame(img, ts, self._t0_wall + ts, self._seq)

    def stop(self) -> None:
        self.cap.release()

    @property
    def finished(self) -> bool:
        return self._finished
