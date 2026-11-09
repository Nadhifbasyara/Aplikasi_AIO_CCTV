"""Perakit Source → Detector (host) → Modul (tracker + analitik) — Fase 6 §5.4."""
import time
from dataclasses import dataclass

from aio_cctv.annotate import Annotator
from aio_cctv.inference.detector import Detector
from aio_cctv.modules.api import Event, ModuleResult
from aio_cctv.modules.host import ModuleRunner, build_context
from aio_cctv.modules.manifest import ModuleManifest
from aio_cctv.zones.models import Profile


@dataclass
class StageTimes:                     # dipakai profiling Fase 9
    detect_ms: float = 0
    module_ms: float = 0


class Pipeline:
    def __init__(self, manifest: ModuleManifest, profile: Profile, frame_wh, fps: float,
                 camera_id: str = "cam", params: dict | None = None, device=None,
                 strict: bool = False, blur: bool = False):
        self.manifest = manifest
        self.detector = Detector.from_manifest(manifest, device)   # Fase 9: InferenceServer
        self.ctx = build_context(manifest, profile, camera_id, frame_wh, fps, params)
        self.module = ModuleRunner(manifest, self.ctx, strict=strict)
        self.annotator = Annotator(profile, frame_wh, manifest, blur=blur)
        self.times = StageTimes()

    def step(self, image, ts) -> ModuleResult:
        t0 = time.perf_counter()
        det = self.detector(image)
        self.times.detect_ms = (time.perf_counter() - t0) * 1e3
        res = self.module.process(image, det, ts)
        self.times.module_ms = self.module.module_ms
        return res

    def annotate(self, image, res: ModuleResult):
        return self.annotator.annotate(image, res)

    def close(self, ts: float) -> list[Event]:
        return self.module.close(ts)
