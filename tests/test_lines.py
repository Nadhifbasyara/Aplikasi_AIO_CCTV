import numpy as np
import supervision as sv

from aio_cctv.analytics.lines import LineCounter

P1, P2 = np.array([0, 100]), np.array([200, 100])     # garis horizontal y = 100


def det_foot(y: float, tid: int = 1) -> sv.Detections:
    """Satu orang dengan titik kaki (bawah-tengah bbox) di (100, y)."""
    return sv.Detections(xyxy=np.array([[95, y - 40, 105, y]], dtype=float),
                         tracker_id=np.array([tid]), class_id=np.array([0]),
                         confidence=np.array([0.9]))


# mendekat dari atas -> berdiri "bergetar" di atas garis -> lanjut ke bawah
FLICKER_PATH = [60, 70, 80, 90, 105, 95, 105, 95, 105, 110, 120, 130, 140]


def run(path, min_cross_frames):
    lc = LineCounter("l1", P1, P2, min_cross_frames=min_cross_frames)
    events = []
    for i, y in enumerate(path):
        events += lc.update(det_foot(y), ts=i * 0.1)
    return lc, events


def test_flicker_is_overcounted_without_threshold():
    _, events = run(FLICKER_PATH, min_cross_frames=1)
    assert len(events) > 1                             # perilaku lama: satu orang dihitung berkali-kali


def test_threshold_counts_single_crossing_once():
    lc, events = run(FLICKER_PATH, min_cross_frames=3)
    assert len(events) == 1
    assert sum(lc.totals) == 1


def test_clean_crossing_still_counted_with_threshold():
    _, events = run([60, 70, 80, 90, 110, 120, 130, 140], min_cross_frames=3)
    assert len(events) == 1


def test_no_detections_is_safe():
    lc = LineCounter("l1", P1, P2, min_cross_frames=3)
    assert lc.update(sv.Detections.empty(), ts=0.0) == []
