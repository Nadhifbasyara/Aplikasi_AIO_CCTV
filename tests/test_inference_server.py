"""InferenceServer bersama + batching (Fase 9 §5.1) dan render hemat FrameHub (§5.5)."""
import threading
import time

import numpy as np
import pytest
import supervision as sv

from aio_cctv.core.framehub import FrameHub
from aio_cctv.inference.server import InferenceServer, ServerPool


class FakeDetector:
    """Mengembalikan satu kotak berisi nilai piksel frame, supaya hasil bisa dicocokkan."""

    def __init__(self, delay_s: float = 0.02, fail: bool = False):
        self.delay_s, self.fail = delay_s, fail
        self.batch_sizes: list[int] = []
        self.warmed = False

    def warmup(self):
        self.warmed = True

    def detect_batch(self, frames):
        if self.fail:
            raise RuntimeError("model rusak")
        self.batch_sizes.append(len(frames))
        time.sleep(self.delay_s)
        return [sv.Detections(xyxy=np.array([[0, 0, 1, 1]], float),
                              confidence=np.array([float(f[0, 0, 0])]),
                              class_id=np.array([0])) for f in frames]


def _frame(v: int) -> np.ndarray:
    return np.full((4, 4, 3), v, np.uint8)


def test_frames_from_many_cameras_are_batched_and_returned_to_the_right_caller():
    det = FakeDetector()
    server = InferenceServer(det, max_batch=8, max_wait_ms=10)
    server.start()
    got = {}

    def camera(i):
        d, _queue_ms, _det_ms = server.submit(_frame(i)).result(timeout=5)
        got[i] = int(d.confidence[0])

    threads = [threading.Thread(target=camera, args=(i,)) for i in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    server.stop()
    assert got == {i: i for i in range(1, 9)}           # hasil tidak tertukar antarkamera
    assert max(det.batch_sizes) > 1                      # benar-benar di-batch
    assert sum(det.batch_sizes) == 8


def test_model_error_reaches_every_camera_in_the_batch():
    server = InferenceServer(FakeDetector(fail=True), max_batch=4, max_wait_ms=5)
    server.start()
    futs = [server.submit(_frame(i)) for i in range(3)]
    for f in futs:
        with pytest.raises(RuntimeError, match="model rusak"):
            f.result(timeout=5)
    server.stop()


def test_pool_shares_one_model_per_key_and_stops_it_after_last_camera():
    made = []

    def make():
        made.append(FakeDetector(delay_s=0))
        return made[-1]

    pool = ServerPool()
    a = pool.acquire(("m.pt",), make, 8, 1)
    b = pool.acquire(("m.pt",), make, 8, 1)
    c = pool.acquire(("lain.pt",), make, 8, 1)
    assert len(made) == 2 and made[0].warmed             # satu model per kunci, sudah warmup
    assert a.server is b.server and a.server is not c.server
    assert int(a(_frame(7)).confidence[0]) == 7
    a.release()
    a.release()                                          # aman dipanggil dua kali
    assert pool.active() == 2
    b.release()
    c.release()
    assert pool.active() == 0
    time.sleep(0.3)
    assert not b.server.is_alive()


def test_framehub_wants_frames_only_when_someone_looks():
    hub = FrameHub()
    assert hub.wanted("c1")                              # belum punya frame: titipkan satu
    hub.put("c1", _frame(1), _frame(1), False)
    assert not hub.wanted("c1")                          # tidak ada yang melihat
    hub.get("c1")                                        # web / thumbnail meminta
    assert hub.wanted("c1")
    assert not hub.wanted("c1", within_s=0)
