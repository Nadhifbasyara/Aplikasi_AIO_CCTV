import time

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from aio_cctv.gui.utils import to_qimage
from aio_cctv.pipeline import Pipeline
from aio_cctv.sources.file_source import FileSource
from aio_cctv.sources.rtsp_source import RTSPSource
from aio_cctv.zones.models import Profile


class PipelineWorker(QThread):
    frame_ready = pyqtSignal(str, QImage)          # cam_id, frame beranotasi
    stats_ready = pyqtSignal(str, dict)            # cam_id, fps/state/latensi
    result_ready = pyqtSignal(str, object)         # cam_id, FrameResult (untuk Fase 6)
    error = pyqtSignal(str, str)

    def __init__(self, cam_id: str, source_uri: str, profile: Profile, settings: dict):
        super().__init__()
        self.cam_id, self.uri, self.profile, self.settings = cam_id, source_uri, profile, settings
        self.max_display_fps = max(1, settings.get("display_fps", 15))
        self._line_names = {ln.id: ln.name for ln in profile.lines}
        self.realtime_file = settings.get("realtime_file", True)

    def _make_source(self):
        if self.uri.startswith("rtsp://"):
            return RTSPSource(self.uri, name=self.cam_id)
        return FileSource(self.uri)

    def run(self) -> None:
        src = None
        try:
            # dibuat di dalam try: FileSource membuka VideoCapture di konstruktor dan
            # bisa gagal untuk path yang salah
            src = self._make_source()
            if src.start() is False:
                self.error.emit(self.cam_id, "Tidak dapat terhubung ke kamera")
            pipe = None
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
                        pipe = Pipeline(self.profile, src.resolution, src.fps or 15,
                                        weights=self.settings.get("weights", "models/yolo11n.pt"),
                                        conf=self.settings.get("conf", 0.35),
                                        device=None if device == "auto" else device,
                                        grace_s=self.settings.get("dwell_grace", 1.5))
                    res = pipe.step(f.image, f.ts)
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
                    self.stats_ready.emit(self.cam_id, {
                        "state": getattr(src, "state", "file"),
                        "proc_fps": n / (now - t_fps),
                        "detect_ms": pipe.times.detect_ms if pipe else 0.0,
                        "persons": len(res.detections) if f is not None else 0,
                        "reconnects": getattr(getattr(src, "stats", None), "reconnects", 0),
                        "lines": {self._line_names.get(k, k): v
                                  for k, v in res.line_totals.items()} if f is not None else {},
                    })
                    n, t_fps = 0, now
        except Exception as e:                     # jangan biarkan thread mati diam-diam
            self.error.emit(self.cam_id, f"{type(e).__name__}: {e}")
        finally:
            if src is not None:
                src.stop()

    def stop(self) -> bool:
        """Minta berhenti; False bila thread belum berhenti dalam batas waktu."""
        self.requestInterruption()
        return self.wait(5000)
