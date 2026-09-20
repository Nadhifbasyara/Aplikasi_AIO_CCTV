import time
from dataclasses import dataclass, field

from aio_cctv.analytics.annotate import Annotator
from aio_cctv.analytics.engine import AnalyticsEngine
from aio_cctv.inference.detector import Detector
from aio_cctv.tracking.tracker import ByteTrackTracker
from aio_cctv.zones.models import Profile


@dataclass
class StageTimes:                     # dipakai profiling Fase 8
    detect_ms: float = 0
    track_ms: float = 0
    analytics_ms: float = 0
    history: list = field(default_factory=list)


class Pipeline:
    def __init__(self, profile: Profile, frame_wh, fps: float, weights="models/yolo11s.pt",
                 conf=0.35, imgsz=640, device=None, grace_s=1.5):
        self.detector = Detector(weights, profile.target_classes, conf, imgsz=imgsz,
                                 device=device)
        self.tracker = ByteTrackTracker(fps)
        self.engine = AnalyticsEngine(profile, frame_wh, grace_s=grace_s, fps=fps)
        self.annotator = Annotator(profile, frame_wh)
        self.times = StageTimes()

    def step(self, image, ts):
        t0 = time.perf_counter()
        det = self.detector(image)
        t1 = time.perf_counter()
        det = self.tracker.update(det)
        t2 = time.perf_counter()
        res = self.engine.update(det, ts)
        t3 = time.perf_counter()
        self.times.detect_ms, self.times.track_ms, self.times.analytics_ms = (
            (t1 - t0) * 1e3, (t2 - t1) * 1e3, (t3 - t2) * 1e3)
        return res

    def annotate(self, image, res):
        return self.annotator.annotate(image, res)
