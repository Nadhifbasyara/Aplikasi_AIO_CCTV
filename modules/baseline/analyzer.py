"""Modul baseline: ByteTrack + dwell, line-crossing, heatmap (kode Fase 3).

BUKAN kontribusi analitik TA ini. Perannya (Fase 6 §5.5): implementasi acuan kontrak,
contoh bagi pembuat modul TA-1/TA-2, pengganti sementara sebelum modul mereka tersedia,
dan pembanding kinerja host di Fase 9–10.
"""
from aio_cctv.modules.api import AnalyticsModule, CameraContext, Event, Metric, ModuleResult

from .dwell import ZoneDwell
from .events import DwellSession
from .heatmap import HeatmapAccumulator
from .lines import LineCounter
from .tracker import ByteTrackTracker


def fmt_duration(sec: float) -> str:
    """Format seperti Proposal §8.2: '1h 15m', '2m 5s', '8s'."""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s"


def dwell_event(s: DwellSession) -> Event:
    return Event("dwell_closed", s.end, s.zone_id, s.track_id,
                 {"start": s.start, "end": s.end, "duration_s": s.duration})


class BaselineModule(AnalyticsModule):
    def __init__(self, ctx: CameraContext):
        super().__init__(ctx)
        p, (w, h) = ctx.params, ctx.frame_wh
        self.tracker = ByteTrackTracker(ctx.fps, p["lost_track_seconds"],
                                        p["track_activation"], p["track_matching"])
        # ambang crossing dalam detik -> frame, agar konsisten di kamera dengan FPS berbeda
        min_cross_frames = max(1, round(ctx.fps * p["line_min_cross_s"])) if ctx.fps else 1
        self.zones = [ZoneDwell(r.id, ctx.to_pixels(r.points), p["grace_s"], p["min_dwell_s"])
                      for r in ctx.zones()]
        self.lines = [LineCounter(r.id, *ctx.to_pixels(r.points), min_cross_frames)
                      for r in ctx.lines()]
        self.heatmap = HeatmapAccumulator(w, h)

    def process(self, frame, detections, ts: float) -> ModuleResult:
        det = self.tracker.update(detections)
        metrics = [Metric("people_count", float(len(det)))]
        events: list[Event] = []
        for zd in self.zones:
            count, closed = zd.update(det, ts)
            metrics.append(Metric("zone_count", float(count), zd.zone_id))
            events += [dwell_event(s) for s in closed]
        for lc in self.lines:
            events += [Event("line_cross", c.ts, c.line_id, c.track_id,
                             {"direction": c.direction}) for c in lc.update(det, ts)]
            n_in, n_out = lc.totals
            metrics += [Metric("line_in", float(n_in), lc.line_id),
                        Metric("line_out", float(n_out), lc.line_id)]
        self.heatmap.update(det)

        labels = {}
        if det.tracker_id is not None:
            for tid in det.tracker_id:
                durs = [d for zd in self.zones if (d := zd.current(int(tid))) is not None]
                if durs:
                    labels[int(tid)] = f"ID {tid} | {fmt_duration(max(durs))}"
        return ModuleResult(det, metrics, events, labels, heat=self.heatmap.acc)

    def close(self, ts: float) -> list[Event]:
        return [dwell_event(s) for zd in self.zones for s in zd.flush()]
