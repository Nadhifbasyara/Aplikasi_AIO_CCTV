from aio_cctv.analytics.dwell import ZoneDwell
from aio_cctv.analytics.events import FrameResult
from aio_cctv.analytics.heatmap import HeatmapAccumulator
from aio_cctv.analytics.lines import LineCounter
from aio_cctv.zones.models import Profile, to_pixels


class AnalyticsEngine:
    """Satu engine untuk semua jenis bisnis; perilakunya 100% dari Profile (Proposal §6)."""

    def __init__(self, profile: Profile, frame_wh: tuple[int, int],
                 grace_s: float = 1.5, min_dwell_s: float = 1.0,
                 fps: float | None = None, line_min_cross_s: float = 0.5):
        w, h = frame_wh
        # ambang crossing dalam detik -> frame, agar konsisten di kamera dengan FPS berbeda
        min_cross_frames = max(1, round(fps * line_min_cross_s)) if fps else 1
        self.profile = profile
        self.zones = [ZoneDwell(z.id, to_pixels(z.polygon, w, h), grace_s, min_dwell_s)
                      for z in profile.zones]
        self.lines = []
        for ln in profile.lines:
            p1, p2 = to_pixels([ln.p1, ln.p2], w, h)
            self.lines.append(LineCounter(ln.id, p1, p2, min_cross_frames))
        self.heatmap = HeatmapAccumulator(w, h)

    def update(self, det, ts: float) -> FrameResult:
        res = FrameResult(ts=ts, detections=det)
        for zd in self.zones:
            count, closed = zd.update(det, ts)
            res.zone_counts[zd.zone_id] = count
            res.closed_sessions += closed
        for lc in self.lines:
            res.crossings += lc.update(det, ts)
            res.line_totals[lc.line_id] = lc.totals
        self.heatmap.update(det)
        if det.tracker_id is not None:
            for tid in det.tracker_id:
                per_zone = {zd.zone_id: d for zd in self.zones if (d := zd.current(int(tid))) is not None}
                if per_zone:
                    res.dwell_now[int(tid)] = per_zone
        return res

    def flush(self):
        out = []
        for zd in self.zones:
            out += zd.flush()
        return out
