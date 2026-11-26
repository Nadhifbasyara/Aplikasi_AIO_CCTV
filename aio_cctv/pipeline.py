"""Perakit Source → Detector (host) → Modul (tracker + analitik) — Fase 6 §5.4."""
import time
from dataclasses import dataclass

import supervision as sv

from aio_cctv.annotate import Annotator
from aio_cctv.inference.detector import Detector
from aio_cctv.modules.api import Event, ModuleResult
from aio_cctv.modules.host import ModuleRunner, build_context
from aio_cctv.modules.manifest import ModuleManifest
from aio_cctv.zones.models import Profile


@dataclass
class StageTimes:                     # dipakai profiling Fase 9
    detect_ms: float = 0              # bagian frame ini dari waktu inferensi (batch dibagi rata)
    module_ms: float = 0
    queue_ms: float = 0               # menunggu giliran di InferenceServer (mode shared)


def detector_key(manifest: ModuleManifest, device) -> tuple:
    """Kamera dengan kunci sama boleh berbagi satu model (Fase 9 §5.1)."""
    m = manifest.model
    return (str(manifest.weights_path), tuple(m.classes or ()), m.conf, m.iou, m.imgsz,
            device or "auto")


class Pipeline:
    def __init__(self, manifest: ModuleManifest, profile: Profile, frame_wh, fps: float,
                 camera_id: str = "cam", params: dict | None = None, device=None,
                 strict: bool = False, blur: bool = False, inference: str = "per_camera",
                 max_batch: int = 8, max_wait_ms: float = 5.0):
        """inference: "per_camera" = model sendiri per kamera (Fase 6, dipakai uji paritas);
        "shared" = InferenceServer bersama + batching lintas kamera (Fase 9 §5.1);
        "deepstream" = deteksi sudah dikerjakan sumber (Jalur B, §5.8), tidak memuat model."""
        self.manifest = manifest
        self.shared = inference == "shared"
        self.external = inference == "deepstream"
        if self.external:
            self.detector = None
        elif self.shared:
            from aio_cctv.inference.server import POOL
            self.detector = POOL.acquire(detector_key(manifest, device),
                                         lambda: Detector.from_manifest(manifest, device),
                                         max_batch, max_wait_ms)
        else:
            self.detector = Detector.from_manifest(manifest, device)
        self.ctx = build_context(manifest, profile, camera_id, frame_wh, fps, params)
        self.module = ModuleRunner(manifest, self.ctx, strict=strict)
        self.annotator = Annotator(profile, frame_wh, manifest, blur=blur)
        self.times = StageTimes()

    def step(self, image, ts, detections=None) -> ModuleResult:
        """detections: hasil deteksi dari sumber (DeepStream); wajib bila inference="deepstream"."""
        t0 = time.perf_counter()
        if self.external:                     # detect_ms diisi worker dari sumber (GPU)
            det = detections if detections is not None else sv.Detections.empty()
        else:
            det = self.detector(image)
            if self.shared:
                self.times.detect_ms = self.detector.detect_ms
                self.times.queue_ms = self.detector.queue_ms
            else:
                self.times.detect_ms = (time.perf_counter() - t0) * 1e3
        res = self.module.process(image, det, ts)
        self.times.module_ms = self.module.module_ms
        return res

    def zone_counts(self, res: ModuleResult | None) -> list[tuple[str, str, int]]:
        return self.annotator.zone_counts(res)

    def annotate(self, image, res: ModuleResult):
        return self.annotator.annotate(image, res)

    def close(self, ts: float) -> list[Event]:
        try:
            return self.module.close(ts)
        finally:
            self.release()

    def release(self) -> None:
        """Lepas model bersama; aman dipanggil berkali-kali."""
        if self.shared:
            self.detector.release()
