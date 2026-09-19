import numpy as np
import supervision as sv

from aio_cctv.analytics.events import CrossingEvent


class LineCounter:
    """Hitung masuk/keluar (Proposal §8.5: sv.LineZone)."""

    def __init__(self, line_id: str, p1: np.ndarray, p2: np.ndarray, min_cross_frames: int = 1):
        """min_cross_frames: orang harus terlihat di sisi seberang garis selama N frame berturut-turut
        sebelum dihitung. Mencegah hitungan "kedip" in/out/in saat orang berdiri di atas garis
        atau bounding box bergetar."""
        self.line_id = line_id
        self.zone = sv.LineZone(start=sv.Point(int(p1[0]), int(p1[1])),
                                end=sv.Point(int(p2[0]), int(p2[1])),
                                triggering_anchors=(sv.Position.BOTTOM_CENTER,),
                                minimum_crossing_threshold=max(1, min_cross_frames))

    def update(self, det: sv.Detections, ts: float) -> list[CrossingEvent]:
        if not len(det) or det.tracker_id is None:
            return []
        crossed_in, crossed_out = self.zone.trigger(det)
        events = [CrossingEvent(self.line_id, int(t), "in", ts) for t in det.tracker_id[crossed_in]]
        events += [CrossingEvent(self.line_id, int(t), "out", ts) for t in det.tracker_id[crossed_out]]
        return events

    @property
    def totals(self) -> tuple[int, int]:
        return self.zone.in_count, self.zone.out_count
