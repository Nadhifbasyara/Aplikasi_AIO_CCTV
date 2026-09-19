import supervision as sv

from aio_cctv.analytics.events import FrameResult
from aio_cctv.zones.models import Profile, to_pixels


def fmt_duration(sec: float) -> str:
    """Format seperti Proposal §8.2: '1h 15m', '2m 5s', '8s'."""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s"


class Annotator:
    def __init__(self, profile: Profile, frame_wh):
        w, h = frame_wh
        self.box = sv.BoxAnnotator(thickness=2, color_lookup=sv.ColorLookup.TRACK)
        self.label = sv.LabelAnnotator(text_scale=0.5, color_lookup=sv.ColorLookup.TRACK)
        self.trace = sv.TraceAnnotator(trace_length=40, color_lookup=sv.ColorLookup.TRACK)
        self.zone_polys = [(z, to_pixels(z.polygon, w, h)) for z in profile.zones]
        self.line_pts = [(ln, to_pixels([ln.p1, ln.p2], w, h)) for ln in profile.lines]

    def annotate(self, frame, res: FrameResult):
        det = res.detections
        img = frame.copy()
        for z, poly in self.zone_polys:
            img = sv.draw_polygon(img, poly, sv.Color.from_hex(z.color), thickness=2)
            cx, cy = poly.mean(axis=0).astype(int)
            img = sv.draw_text(img, f"{z.name}: {res.zone_counts.get(z.id, 0)}",
                               sv.Point(int(cx), int(cy)), background_color=sv.Color.from_hex(z.color))
        for ln, (p1, p2) in self.line_pts:
            tin, tout = res.line_totals.get(ln.id, (0, 0))
            img = sv.draw_line(img, sv.Point(*map(int, p1)), sv.Point(*map(int, p2)),
                               sv.Color.from_hex(ln.color), thickness=3)
            img = sv.draw_text(img, f"{ln.name} IN {tin} / OUT {tout}", sv.Point(*map(int, p1)),
                               background_color=sv.Color.from_hex(ln.color))
        if len(det) and det.tracker_id is not None:
            labels = []
            for tid in det.tracker_id:
                dz = res.dwell_now.get(int(tid))
                labels.append(f"ID {tid} | {fmt_duration(max(dz.values()))}" if dz else f"ID {tid}")
            img = self.trace.annotate(img, det)
            img = self.box.annotate(img, det)
            img = self.label.annotate(img, det, labels=labels)
        return img
