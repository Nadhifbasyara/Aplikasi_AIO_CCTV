import numpy as np
import supervision as sv

from aio_cctv.analytics.events import DwellSession


class ZoneDwell:
    """Sesi dwell per track di satu zona, berbasis timestamp + grace period."""

    def __init__(self, zone_id: str, polygon_px: np.ndarray, grace_s: float = 1.5,
                 min_dwell_s: float = 1.0, anchor: sv.Position = sv.Position.BOTTOM_CENTER):
        self.zone_id = zone_id
        self.zone = sv.PolygonZone(polygon=polygon_px, triggering_anchors=(anchor,))
        self.grace_s, self.min_dwell_s = grace_s, min_dwell_s
        self.active: dict[int, list[float]] = {}      # tid -> [start, last_seen]

    def update(self, det: sv.Detections, ts: float) -> tuple[int, list[DwellSession]]:
        inside_ids: list[int] = []
        if len(det) and det.tracker_id is not None:
            mask = self.zone.trigger(det)
            inside_ids = [int(t) for t in det.tracker_id[mask]]
        for tid in inside_ids:
            if tid in self.active:
                self.active[tid][1] = ts
            else:
                self.active[tid] = [ts, ts]

        closed = []
        for tid, (start, last) in list(self.active.items()):
            if ts - last > self.grace_s:               # sudah keluar / hilang > grace
                del self.active[tid]
                if last - start >= self.min_dwell_s:   # buang "lewat sekilas"
                    closed.append(DwellSession(self.zone_id, tid, start, last))
        return len(inside_ids), closed

    def current(self, tid: int) -> float | None:
        s = self.active.get(tid)
        return None if s is None else s[1] - s[0]

    def flush(self) -> list[DwellSession]:
        """Tutup semua sesi aktif (akhir video / kamera dimatikan)."""
        out = [DwellSession(self.zone_id, t, s, e) for t, (s, e) in self.active.items()
               if e - s >= self.min_dwell_s]
        self.active.clear()
        return out
