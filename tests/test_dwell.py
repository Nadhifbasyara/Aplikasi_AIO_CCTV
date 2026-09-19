import numpy as np
import supervision as sv

from aio_cctv.analytics.dwell import ZoneDwell

POLY = np.array([[0, 0], [100, 0], [100, 100], [0, 100]])


def det_at(x, y, tid):
    return sv.Detections(xyxy=np.array([[x - 5, y - 20, x + 5, y]], dtype=float),
                         tracker_id=np.array([tid]), class_id=np.array([0]),
                         confidence=np.array([0.9]))


def test_session_closed_after_grace():
    zd = ZoneDwell("z", POLY, grace_s=1.0, min_dwell_s=0.5)
    for t in np.arange(0, 5.0, 0.1):          # di dalam zona selama 5 detik
        zd.update(det_at(50, 50, 1), t)
    _, closed = zd.update(sv.Detections.empty(), 6.5)   # keluar > grace
    assert len(closed) == 1 and abs(closed[0].duration - 4.9) < 0.11


def test_short_occlusion_does_not_split_session():
    zd = ZoneDwell("z", POLY, grace_s=1.5)
    for t in np.arange(0, 3, 0.1):
        zd.update(det_at(50, 50, 1), t)
    zd.update(sv.Detections.empty(), 3.8)       # hilang 0.9 s (< grace)
    for t in np.arange(4, 6, 0.1):
        zd.update(det_at(50, 50, 1), t)
    assert zd.current(1) > 5.5                  # masih satu sesi
