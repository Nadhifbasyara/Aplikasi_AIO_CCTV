import time
from collections import defaultdict

import supervision as sv
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from aio_cctv.alerts.rules import Rule, RuleEngine
from aio_cctv.annotate import fmt_value
from aio_cctv.gui.utils import to_qimage
from aio_cctv.modules.api import ModuleResult
from aio_cctv.modules.host import ModuleError
from aio_cctv.modules.registry import ModuleInfo
from aio_cctv.pipeline import Pipeline
from aio_cctv.sources.file_source import FileSource
from aio_cctv.sources.rtsp_source import RTSPSource
from aio_cctv.sources.url_builder import mask
from aio_cctv.storage.aggregator import Aggregator, HealthRecorder, record_alert
from aio_cctv.zones.models import Profile


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


class PipelineWorker(QThread):
    frame_ready = pyqtSignal(str, QImage)          # cam_id, frame beranotasi
    stats_ready = pyqtSignal(str, dict)            # cam_id, fps/state/latensi
    result_ready = pyqtSignal(str, object)         # cam_id, ModuleResult
    alert_raised = pyqtSignal(str, dict)           # cam_id, alert (sudah tertulis ke DB)
    error = pyqtSignal(str, str)

    def __init__(self, cam_id: str, source_uri: str, profile: Profile, settings: dict,
                 module: ModuleInfo | None, module_id: str = "", module_params: dict | None = None,
                 storage=None, rules: list[Rule] | None = None, frames=None):
        super().__init__()
        self.cam_id, self.uri, self.profile, self.settings = cam_id, source_uri, profile, settings
        self.module, self.module_id = module, module_id
        self.module_params = module_params or {}
        self.storage = storage                     # StorageWriter bersama, atau None (tanpa DB)
        self.frames = frames                       # FrameHub bersama (web & ekspor), atau None
        self.rules = RuleEngine(rules or [])
        self.region_of_role: dict[str, set[str]] = defaultdict(set)
        for shape in [*profile.zones, *profile.lines]:
            if shape.role:
                self.region_of_role[shape.role].add(shape.id)
        self.max_display_fps = max(1, settings.get("display_fps", 15))
        self.realtime_file = settings.get("realtime_file", True)
        # Fase 9 §5.4: analisis tidak perlu lebih cepat dari ini (0 = ikuti FPS kamera)
        self.target_fps = float(settings.get("target_fps", 0) or 0)
        # Fase 9 §5.5: diisi Live View; False = tile tidak terlihat → tidak perlu anotasi/QImage
        self.gui_visible = True

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
        src, pipe, last_ts, last_wall, latency_ms = None, None, 0.0, time.time(), 0.0
        agg = health = None
        if self.storage is not None:
            agg = Aggregator(self.cam_id, self.module.manifest.id, self.storage)
            health = HealthRecorder(self.cam_id, self.storage)
        try:
            # dibuat di dalam try: FileSource membuka VideoCapture di konstruktor dan
            # bisa gagal untuk path yang salah
            src = self._make_source()
            if src.start() is False:
                self.error.emit(self.cam_id, "Tidak dapat terhubung ke kamera")
            res = None
            last_emit, n, t_fps = 0.0, 0, time.monotonic()
            lat_window: list[float] = []           # latensi tiap frame dalam jendela 1 detik
            render_ms = 0.0
            is_rtsp = isinstance(src, RTSPSource)
            interval = 1.0 / self.target_fps if self.target_fps > 0 else 0.0
            next_due = 0.0
            while not self.isInterruptionRequested():
                t_frame = time.monotonic()
                if is_rtsp and interval and t_frame < next_due:
                    # belum jadwalnya: frame yang datang di sela ini dilewati tanpa dikonversi
                    # maupun dideteksi (latest-frame), FPS analisis = target_fps
                    self.msleep(max(1, int((next_due - t_frame) * 1000)))
                    f = None
                else:
                    f = src.read()
                if f is not None and interval:     # jadwal tetap; bila tertinggal, mulai lagi
                    next_due += interval           # dari sekarang (jangan menumpuk antrean)
                    if next_due <= t_frame:
                        next_due = t_frame + interval
                if f is None:
                    if src.finished and isinstance(src, FileSource):
                        break
                    self.msleep(3)
                else:
                    if pipe is None:               # dibuat di thread ini (model + resolusi aktual)
                        device = self.settings.get("device", "auto")
                        fps = src.fps or 15
                        if is_rtsp and self.target_fps > 0:
                            fps = min(fps, self.target_fps)   # FPS EFEKTIF untuk modul (§5.4)
                        pipe = Pipeline(self.module.manifest, self.profile, src.resolution,
                                        fps, camera_id=self.cam_id,
                                        params=self.module_params,
                                        device=None if device == "auto" else device,
                                        blur=self.settings.get("blur", False),
                                        inference=self.settings.get("inference", "shared"),
                                        max_batch=int(self.settings.get("max_batch", 8)),
                                        max_wait_ms=float(self.settings.get("max_wait_ms", 5)))
                    res = pipe.step(f.image, f.ts)
                    last_ts, last_wall = f.ts, f.wall_ts
                    if isinstance(src, RTSPSource):  # ts RTSP = monotonic saat frame diterima
                        latency_ms = (time.monotonic() - f.ts) * 1e3
                        lat_window.append(latency_ms)
                    if agg is not None:
                        agg.consume(res, f.ts, f.wall_ts)
                        for a in self.rules.evaluate(res, f.ts, self.region_of_role):
                            self.alert_raised.emit(self.cam_id, record_alert(
                                self.storage, self.cam_id, a, f.wall_ts - (f.ts - a.ts)))
                    self.result_ready.emit(self.cam_id, res)
                    n += 1
                    hub_wants = self.frames is not None and self.frames.wanted(self.cam_id)
                    if (self.gui_visible or hub_wants) and \
                            time.monotonic() - last_emit >= 1.0 / self.max_display_fps:
                        t_r = time.perf_counter()
                        vis = pipe.annotate(f.image, res)
                        if self.gui_visible:       # batasi repaint GUI
                            self.frame_ready.emit(self.cam_id, to_qimage(vis))
                        render_ms = (time.perf_counter() - t_r) * 1e3
                        if hub_wants:
                            self.frames.put(self.cam_id, vis, f.image,
                                            bool(self.settings.get("blur", False)))
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
                    stats = {
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
                        "queue_ms": pipe.times.queue_ms if pipe else 0.0,
                        "module": self.module.manifest.name,
                        "tracks": len(res.tracks) if res is not None else 0,
                        "metrics": self._camera_metrics(res),
                        # nilai mentah terakhir [key, region_id, value] -> KPI "sekarang"
                        "live_metrics": [[m.key, m.region_id, float(m.value)]
                                         for m in res.metrics] if res is not None else [],
                        "reconnects": getattr(stream_stats, "reconnects", 0),
                        "downtime_s": getattr(stream_stats, "downtime_s", 0.0),
                        "latency_ms": latency_ms,
                        # Fase 9 §3.1: rincian waktu per tahap untuk benchmark kapasitas
                        "latency_p95_ms": _p95(lat_window),
                        "decode_ms": getattr(stream_stats, "decode_ms", 0.0),
                        "convert_ms": getattr(stream_stats, "convert_ms", 0.0),
                        "target_fps": min(self.target_fps, getattr(src, "fps", 0) or 1e9)
                        if is_rtsp and self.target_fps > 0 else getattr(src, "fps", 0.0),
                        "render_ms": render_ms,
                    }
                    lat_window = []
                    self.stats_ready.emit(self.cam_id, stats)
                    if health is not None:
                        health.add(stats, time.time())
                    n, t_fps = 0, now
            if pipe is not None:                   # tutup sesi modul yang masih terbuka
                pipe_, pipe = pipe, None
                closing = pipe_.close(last_ts)
                if agg is not None:
                    agg.events(closing, last_ts, last_wall)
                self.result_ready.emit(self.cam_id, ModuleResult(sv.Detections.empty(),
                                                                 events=closing))
        except ModuleError as e:                   # hanya kamera ini yang berhenti
            self.error.emit(self.cam_id, mask(f"Modul error — {e}"))
        except Exception as e:                     # jangan biarkan thread mati diam-diam
            # pesan bisa memuat URL sumber; tampil di tile, kartu kamera, dan dashboard web
            self.error.emit(self.cam_id, mask(f"{type(e).__name__}: {e}"))
        finally:
            if pipe is not None:                   # error di tengah jalan: lepas model bersama
                pipe.release()
            if self.frames is not None:
                self.frames.drop(self.cam_id)
            if agg is not None:                    # menit/jam yang belum lengkap tetap tersimpan
                agg.flush()
                health.flush()
            if src is not None:
                src.stop()

    def stop(self) -> bool:
        """Minta berhenti; False bila thread belum berhenti dalam batas waktu."""
        self.requestInterruption()
        return self.wait(5000)
