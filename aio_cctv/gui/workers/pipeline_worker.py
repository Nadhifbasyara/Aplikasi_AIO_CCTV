import time

import supervision as sv
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from aio_cctv.annotate import fmt_value
from aio_cctv.gui.utils import to_qimage
from aio_cctv.modules.api import ModuleResult
from aio_cctv.modules.host import ModuleError
from aio_cctv.modules.registry import ModuleInfo
from aio_cctv.pipeline import Pipeline
from aio_cctv.sources.file_source import FileSource
from aio_cctv.sources.rtsp_source import RTSPSource
from aio_cctv.zones.models import Profile


class PipelineWorker(QThread):
    frame_ready = pyqtSignal(str, QImage)          # cam_id, frame beranotasi
    stats_ready = pyqtSignal(str, dict)            # cam_id, fps/state/latensi
    result_ready = pyqtSignal(str, object)         # cam_id, ModuleResult (untuk Fase 7)
    error = pyqtSignal(str, str)

    def __init__(self, cam_id: str, source_uri: str, profile: Profile, settings: dict,
                 module: ModuleInfo | None, module_id: str = "", module_params: dict | None = None):
        super().__init__()
        self.cam_id, self.uri, self.profile, self.settings = cam_id, source_uri, profile, settings
        self.module, self.module_id = module, module_id
        self.module_params = module_params or {}
        self.max_display_fps = max(1, settings.get("display_fps", 15))
        self.realtime_file = settings.get("realtime_file", True)

    def _make_source(self):
        if self.uri.startswith("rtsp://"):
            return RTSPSource(self.uri, name=self.cam_id)
        return FileSource(self.uri)

    def _module_problem(self) -> str:
        if self.module is None:
            return f"Modul '{self.module_id}' tidak terpasang — pilih modul lain di dialog kamera"
        if not self.module.valid:
            return f"Modul '{self.module.id}' tidak valid: {'; '.join(self.module.errors)}"
        return ""

    def _camera_metrics(self, res: ModuleResult | None) -> dict[str, str]:
        """Metrik tingkat kamera (tanpa zona) untuk footer tile, berlabel dari manifest."""
        if res is None:
            return {}
        m = self.module.manifest
        out = {}
        for mt in res.metrics:
            if mt.region_id is None and (spec := m.metric(mt.key)) is not None:
                out[spec.label or mt.key] = f"{fmt_value(mt.value)}{spec.unit and ' ' + spec.unit}"
        return out

    def run(self) -> None:
        if problem := self._module_problem():      # jangan buka koneksi kamera untuk percuma
            self.error.emit(self.cam_id, problem)
            return
        src, pipe, last_ts = None, None, 0.0
        try:
            # dibuat di dalam try: FileSource membuka VideoCapture di konstruktor dan
            # bisa gagal untuk path yang salah
            src = self._make_source()
            if src.start() is False:
                self.error.emit(self.cam_id, "Tidak dapat terhubung ke kamera")
            res = None
            last_emit, n, t_fps = 0.0, 0, time.monotonic()
            while not self.isInterruptionRequested():
                t_frame = time.monotonic()
                f = src.read()
                if f is None:
                    if src.finished and isinstance(src, FileSource):
                        break
                    self.msleep(3)
                else:
                    if pipe is None:               # dibuat di thread ini (model + resolusi aktual)
                        device = self.settings.get("device", "auto")
                        pipe = Pipeline(self.module.manifest, self.profile, src.resolution,
                                        src.fps or 15, camera_id=self.cam_id,
                                        params=self.module_params,
                                        device=None if device == "auto" else device)
                    res = pipe.step(f.image, f.ts)
                    last_ts = f.ts
                    self.result_ready.emit(self.cam_id, res)
                    n += 1
                    if time.monotonic() - last_emit >= 1.0 / self.max_display_fps:
                        self.frame_ready.emit(self.cam_id,           # batasi beban repaint GUI
                                              to_qimage(pipe.annotate(f.image, res)))
                        last_emit = time.monotonic()
                    if self.realtime_file and isinstance(src, FileSource):
                        # tanpa ini file diputar secepat inferensi (§5.2); demo harus
                        # terlihat seperti kamera live
                        spare = 1.0 / (src.fps or 25.0) - (time.monotonic() - t_frame)
                        if spare > 0:
                            self.msleep(int(spare * 1000))

                # di luar cabang di atas: kamera yang belum/tidak terhubung pun harus
                # tetap melaporkan state-nya supaya tile bisa menampilkan "reconnecting"
                now = time.monotonic()
                if now - t_fps >= 1.0:
                    stream_stats = getattr(src, "stats", None)
                    frames_rx = getattr(stream_stats, "frames_received", 0)
                    frames_cx = getattr(stream_stats, "frames_consumed", 0)
                    self.stats_ready.emit(self.cam_id, {
                        "state": getattr(src, "state", "file"),
                        "stream_type": "RTSP/TCP" if isinstance(src, RTSPSource) else "File",
                        "bandwidth_kbps": getattr(stream_stats, "bandwidth_kbps", 0.0),
                        "total_frames": frames_rx,
                        "decoded_frames": frames_cx,
                        "dropped_rate": getattr(stream_stats, "dropped_rate", 0.0),
                        "proc_fps": n / (now - t_fps),
                        "stream_fps": getattr(stream_stats, "recv_fps", getattr(src, "fps", 0.0)),
                        "resolution": getattr(src, "resolution", (0, 0)),
                        "detect_ms": pipe.times.detect_ms if pipe else 0.0,
                        "module_ms": pipe.times.module_ms if pipe else 0.0,
                        "module": self.module.manifest.name,
                        "tracks": len(res.tracks) if res is not None else 0,
                        "metrics": self._camera_metrics(res),
                        "reconnects": getattr(stream_stats, "reconnects", 0),
                        "downtime_s": getattr(stream_stats, "downtime_s", 0.0),
                    })
                    n, t_fps = 0, now
            if pipe is not None:                   # tutup sesi modul yang masih terbuka
                pipe_, pipe = pipe, None
                self.result_ready.emit(self.cam_id, ModuleResult(sv.Detections.empty(),
                                                                 events=pipe_.close(last_ts)))
        except ModuleError as e:                   # hanya kamera ini yang berhenti
            self.error.emit(self.cam_id, f"Modul error — {e}")
        except Exception as e:                     # jangan biarkan thread mati diam-diam
            self.error.emit(self.cam_id, f"{type(e).__name__}: {e}")
        finally:
            if src is not None:
                src.stop()

    def stop(self) -> bool:
        """Minta berhenti; False bila thread belum berhenti dalam batas waktu."""
        self.requestInterruption()
        return self.wait(5000)
