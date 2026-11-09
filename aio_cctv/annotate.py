"""Overlay generik (Fase 6 §5.4): zona/garis dari profil, track & label & metrik dari modul.

Tidak tahu arti metrik apa pun — label dan satuan diambil dari manifest modul.
"""
import cv2
import numpy as np
import supervision as sv

from aio_cctv.modules.api import ModuleResult
from aio_cctv.modules.manifest import ModuleManifest
from aio_cctv.zones.models import Profile, to_pixels


def fmt_value(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def render_heat(grid: np.ndarray, frame_wh, background: np.ndarray | None = None,
                alpha: float = 0.55) -> np.ndarray:
    """Grid akumulasi ≥ 0 (ModuleResult.heat) -> citra heatmap seukuran frame."""
    w, h = frame_wh
    hm = cv2.resize(grid.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    hm = cv2.GaussianBlur(hm, (0, 0), sigmaX=3 * w / grid.shape[1])
    hm = cv2.normalize(hm, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    color = cv2.applyColorMap(hm, cv2.COLORMAP_JET)
    if background is None:
        return color
    return cv2.addWeighted(color, alpha, background, 1 - alpha, 0)


class Annotator:
    def __init__(self, profile: Profile, frame_wh, manifest: ModuleManifest, blur: bool = False):
        w, h = frame_wh
        self.manifest = manifest
        # mode anonim (Fase 7 §3.7): kotak orang diburamkan sebelum frame sampai ke GUI
        self.blur = sv.BlurAnnotator(kernel_size=max(15, w // 40)) if blur else None
        self.box = sv.BoxAnnotator(thickness=2, color_lookup=sv.ColorLookup.TRACK)
        self.label = sv.LabelAnnotator(text_scale=0.5, color_lookup=sv.ColorLookup.TRACK)
        self.trace = sv.TraceAnnotator(trace_length=40, color_lookup=sv.ColorLookup.TRACK)
        self.plain_box = sv.BoxAnnotator(thickness=2)      # modul tanpa tracker_id
        self.zone_polys = [(z, to_pixels(z.polygon, w, h)) for z in profile.zones]
        self.line_pts = [(ln, to_pixels([ln.p1, ln.p2], w, h)) for ln in profile.lines]

    def _region_text(self, name: str, region_id: str, res: ModuleResult) -> str:
        parts = []
        for m in res.metrics:
            if m.region_id == region_id and (spec := self.manifest.metric(m.key)) is not None:
                parts.append(f"{spec.label or m.key} {fmt_value(m.value)}{spec.unit and ' ' + spec.unit}")
        return f"{name}: {' · '.join(parts)}" if parts else name

    def annotate(self, frame: np.ndarray, res: ModuleResult) -> np.ndarray:
        det = res.tracks
        img = frame.copy()
        if self.blur is not None and len(det):
            img = self.blur.annotate(img, det)
        for z, poly in self.zone_polys:
            color = sv.Color.from_hex(z.color)
            img = sv.draw_polygon(img, poly, color, thickness=2)
            cx, cy = poly.mean(axis=0).astype(int)
            img = sv.draw_text(img, self._region_text(z.name, z.id, res),
                               sv.Point(int(cx), int(cy)), background_color=color)
        for ln, (p1, p2) in self.line_pts:
            color = sv.Color.from_hex(ln.color)
            img = sv.draw_line(img, sv.Point(*map(int, p1)), sv.Point(*map(int, p2)), color,
                               thickness=3)
            img = sv.draw_text(img, self._region_text(ln.name, ln.id, res),
                               sv.Point(*map(int, p1)), background_color=color)
        if not len(det):
            return img
        if det.tracker_id is None:
            return self.plain_box.annotate(img, det)
        labels = [res.labels.get(int(t), f"ID {t}") for t in det.tracker_id]
        img = self.trace.annotate(img, det)
        img = self.box.annotate(img, det)
        return self.label.annotate(img, det, labels=labels)
