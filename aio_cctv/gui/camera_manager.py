import json
import logging
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from aio_cctv.alerts.rules import RuleEngine, parse_rules
from aio_cctv.core import secrets
from aio_cctv.core.framehub import FrameHub
from aio_cctv.core.paths import CAMERAS_FILE, DB_FILE, PROFILES_DIR
from aio_cctv.gui.utils import app_settings
from aio_cctv.gui.workers.pipeline_worker import PipelineWorker
from aio_cctv.modules.registry import DEFAULT_MODULE, ModuleInfo, discover
from aio_cctv.sources.url_builder import build_url, mask
from aio_cctv.storage.aggregator import record_alert
from aio_cctv.storage.db import purge
from aio_cctv.storage.writer import StorageWriter
from aio_cctv.web.state import CameraView, WebState
from aio_cctv.zones.models import Profile, SourceConfig

log = logging.getLogger(__name__)
INS_SYSTEM = ("INSERT OR REPLACE INTO system_minute(minute_ts,cpu_pct,ram_mb,gpu_pct,vram_mb,"
              "cams_active) VALUES (?,?,?,?,?,?)")
HOST_CHECK_MS = 5000
SYSTEM_SAMPLE_MS = 5000


@dataclass
class CameraConfig:
    id: str
    name: str
    brand: str               # kunci camera_brands.json, "file", atau "webcam"
    host: str = ""
    port: int | None = None
    user: str = ""
    stream: str = "sub"      # analitik default di sub-stream (Fase 4 §5.7)
    file_path: str = ""      # path video, atau indeks webcam ("0")
    url: str = ""            # hanya untuk brand "custom": URL RTSP lengkap tanpa kredensial
    profile_id: str = ""
    enabled: bool = True
    location: str = ""       # keterangan bebas, mis. "Indoor", "Lantai 2" (label di kartu kamera)
    # Fase 8: modul, parameter modul, dan aturan alert disimpan di Profile, bukan di sini.


LEGACY_KEYS = ("module_id", "module_params", "rules")   # cameras.json Fase 6–7


def with_credentials(url: str, user: str, password: str) -> str:
    """Sisipkan user/password ke URL manual saat dipakai; keduanya tidak ikut tersimpan."""
    if not user or not url:
        return url
    u = urlsplit(url)
    host = u.netloc.rsplit("@", 1)[-1]                  # buang kredensial lama bila ada
    cred = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    return urlunsplit((u.scheme, cred + host, u.path, u.query, u.fragment))


def source_config(cam: CameraConfig) -> dict:
    """Deskripsi sumber untuk Profile — tanpa kredensial (DoD: password hanya di keyring)."""
    if cam.brand == "webcam":
        return {"type": "webcam", "uri": cam.file_path}
    if cam.brand == "file":
        return {"type": "file", "uri": cam.file_path}
    return {"type": "rtsp", "uri": mask(build_source_uri(cam, "_"))}


def build_source_uri(cam: CameraConfig, password: str) -> str:
    """URI sumber lengkap. Dipisah dari CameraManager agar dialog bisa memakai
    password yang baru diketik (belum masuk keyring)."""
    if cam.brand in ("file", "webcam"):
        return cam.file_path
    if cam.brand == "custom":
        return with_credentials(cam.url, cam.user, password)
    return build_url(cam.brand, cam.host, cam.user, password, cam.stream, cam.port)


class CameraManager(QObject):
    cameras_changed = pyqtSignal()
    modules_changed = pyqtSignal()
    worker_started = pyqtSignal(str)
    worker_stopped = pyqtSignal(str)
    alert_raised = pyqtSignal(str, dict)       # cam_id, alert (sudah tertulis ke DB)
    stats_updated = pyqtSignal(str, dict)      # cam_id, statistik worker (tiap detik)

    def __init__(self, db_path=DB_FILE):
        super().__init__()
        self.cameras: dict[str, CameraConfig] = {}
        self.workers: dict[str, PipelineWorker] = {}
        self._retired: list[PipelineWorker] = []   # thread yang belum mau berhenti
        self.modules: dict[str, ModuleInfo] = discover()
        self.load()

        # Fase 7: satu writer SQLite untuk semua kamera + aturan host + sumber daya PC
        self.db_path = db_path
        self.frames = FrameHub()                   # frame terakhir per kamera (web, ekspor)
        self.storage = StorageWriter(db_path)      # membuat skema bila DB belum ada
        try:
            if removed := purge(db_path, app_settings()["retention_days"]):
                log.info("retensi data: %s", removed)
        except Exception as e:                     # DB terkunci: coba lagi saat dibuka berikutnya
            log.warning("retensi data dilewati: %s", e)
        self.storage.start()

        # Fase 8: dashboard web lokal — state aman-thread, server dijalankan start_web()
        self.web_state = WebState(db_path, self.frames)
        self.web = None
        self.web_error = ""
        self._sync_web()
        self.cameras_changed.connect(self._sync_web)
        self.modules_changed.connect(self._sync_web)
        self.worker_started.connect(lambda cid: self.web_state.set_running(cid, True))
        self.worker_stopped.connect(lambda cid: self.web_state.set_running(cid, False))

        self._host_rules: dict[str, RuleEngine] = {}
        self._last_stats: dict[str, dict] = {}
        self._host_timer = QTimer(self)
        self._host_timer.timeout.connect(self._check_host_rules)
        self._host_timer.start(HOST_CHECK_MS)
        self._sys_samples: list[dict] = []
        self._sys_minute: int | None = None
        from aio_cctv.core.sysmon import SystemSampler
        self._sampler = SystemSampler()            # dibuat dini: sampel CPU pertama bermakna
        self._sys_timer = QTimer(self)
        self._sys_timer.timeout.connect(self._sample_system)
        self._sys_timer.start(SYSTEM_SAMPLE_MS)

    def reload_modules(self) -> None:
        """Pindai ulang folder modul (setelah memasang modul). Kamera yang sedang berjalan
        tetap memakai modul yang sudah dimuat sampai dijalankan ulang."""
        self.modules = discover()
        self.modules_changed.emit()

    # ------------------------------------------------------------ profil (Fase 8)
    @staticmethod
    def profile_path(cam: CameraConfig) -> Path:
        return PROFILES_DIR / f"{cam.profile_id or cam.id}.json"

    def profile(self, cam: CameraConfig) -> Profile:
        """Profil kamera; kamera yang zonanya belum digambar tetap boleh jalan."""
        path = self.profile_path(cam)
        if path.exists():
            try:
                return Profile.load(path)
            except Exception as e:                 # profil rusak: jangan jatuhkan aplikasi
                log.warning("profil %s tidak terbaca (%s); memakai profil kosong", path.name, e)
        return Profile(profile_id=path.stem, name=cam.name,
                       source=SourceConfig(**source_config(cam)))

    def save_profile(self, cam: CameraConfig, profile: Profile) -> None:
        profile.save(self.profile_path(cam))
        if cam.profile_id != self.profile_path(cam).stem:
            cam.profile_id = self.profile_path(cam).stem
        self.save()                                # memicu cameras_changed (halaman lain segar)

    def module_id(self, cam: CameraConfig) -> str:
        return self.profile(cam).module.module_id

    def module_for(self, cam: CameraConfig) -> ModuleInfo | None:
        return self.modules.get(self.module_id(cam))

    def load(self) -> None:
        if not CAMERAS_FILE.exists():
            return
        known = {f.name for f in fields(CameraConfig)}
        migrated = False
        for c in json.loads(CAMERAS_FILE.read_text()):
            legacy = {k: c[k] for k in LEGACY_KEYS if k in c}
            cam = CameraConfig(**{k: v for k, v in c.items() if k in known})
            self.cameras[cam.id] = cam
            if legacy:
                self._migrate_camera(cam, legacy)
                migrated = True
        if migrated:                               # tulis ulang cameras.json tanpa kunci lama
            CAMERAS_FILE.write_text(json.dumps([asdict(c) for c in self.cameras.values()],
                                               indent=2))

    def _migrate_camera(self, cam: CameraConfig, legacy: dict) -> None:
        """Fase 6–7 menyimpan modul & aturan di cameras.json; Fase 8 memindahkannya ke profil.
        Profil yang sudah v2 (punya kunci "module") tidak ditimpa."""
        path = self.profile_path(cam)
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            raw = {}
        if "module" in raw:
            return
        profile = self.profile(cam)
        profile.module.module_id = legacy.get("module_id") or DEFAULT_MODULE
        profile.module.params = dict(legacy.get("module_params") or {})
        try:
            profile.rules = parse_rules(legacy["rules"]) if legacy.get("rules") else []
        except Exception as e:
            log.warning("aturan lama kamera %s tidak valid (%s); dibuang", cam.id, e)
        profile.save(path)
        cam.profile_id = path.stem
        log.info("kamera %s: modul & aturan dipindah ke profil %s", cam.id, path.name)

    def save(self) -> None:
        CAMERAS_FILE.write_text(json.dumps([asdict(c) for c in self.cameras.values()], indent=2))
        self.cameras_changed.emit()

    def source_uri(self, cam: CameraConfig) -> str:
        pw = secrets.get_password(cam.id)          # dari OS keyring, bukan dari file
        return build_source_uri(cam, pw)

    def is_running(self, cam_id: str) -> bool:
        return cam_id in self.workers

    def start(self, cam_id: str, settings: dict | None = None) -> PipelineWorker:
        if (running := self.workers.get(cam_id)) is not None:
            return running
        cam = self.cameras[cam_id]
        profile = self.profile(cam)
        rules = profile.rules or parse_rules(None)  # kosong = aturan bawaan host
        self._host_rules[cam_id] = RuleEngine(rules)
        self._last_stats.pop(cam_id, None)
        mid = profile.module.module_id
        w = PipelineWorker(cam_id, self.source_uri(cam), profile, settings or app_settings(),
                           self.modules.get(mid), mid, profile.module.params,
                           storage=self.storage, rules=rules, frames=self.frames)
        w.finished.connect(lambda cid=cam_id, worker=w: self._on_worker_finished(cid, worker))
        w.stats_ready.connect(self._on_stats)
        w.error.connect(self._on_error)
        w.alert_raised.connect(self.alert_raised)
        self.workers[cam_id] = w
        self.worker_started.emit(cam_id)           # penonton menyambung sinyal dulu…
        w.start()                                  # …baru thread dijalankan
        return w

    def _on_worker_finished(self, cam_id: str, worker: PipelineWorker) -> None:
        """Worker berhenti sendiri (file habis / error fatal).

        QThread.finished sampai ke thread GUI secara queued, jadi pada restart cepat
        sinyal milik worker LAMA bisa tiba setelah worker baru terdaftar — tanpa
        pemeriksaan identitas ini, worker baru akan dicoret dari daftar dan berjalan
        tanpa tercatat (tidak ikut dihentikan saat aplikasi ditutup).
        """
        if self.workers.get(cam_id) is worker:
            del self.workers[cam_id]
            self.worker_stopped.emit(cam_id)

    def stop(self, cam_id: str) -> None:
        if (w := self.workers.pop(cam_id, None)) is None:
            return
        if not w.stop():
            # belum berhenti dalam batas waktu: tahan referensinya, kalau di-GC saat
            # masih jalan Qt akan membatalkan proses ("Destroyed while still running")
            self._retired.append(w)
        self._retired = [r for r in self._retired if r.isRunning()]
        self.worker_stopped.emit(cam_id)

    def stop_all(self) -> None:
        for cid in list(self.workers):
            self.stop(cid)

    # ------------------------------------------------------------ dashboard web (Fase 8)
    def _sync_web(self) -> None:
        self.web_state.set_modules(self.modules)
        self.web_state.set_cameras([CameraView(c.id, c.name, self.module_id(c), self.profile_path(c))
                                    for c in self.cameras.values()])

    def start_web(self, settings: dict | None = None) -> bool:
        from aio_cctv.web.auth import web_password_hash
        from aio_cctv.web.server import WebServer

        self.stop_web()
        s = settings or app_settings()
        if not s["web_enabled"]:
            self.web_error = ""
            return False
        self.web_state.auth_required = bool(s["web_lan"])
        self.web_state.password_hash = web_password_hash() if s["web_lan"] else ""
        server = WebServer(self.web_state, int(s["web_port"]), bool(s["web_lan"]))
        if server.start():
            self.web, self.web_error = server, ""
            return True
        self.web_error = server.error
        return False

    def stop_web(self) -> None:
        if self.web is not None:
            self.web.stop()
            self.web = None

    def shutdown(self) -> None:
        """Dipanggil saat aplikasi ditutup: worker berhenti dulu (flush agregat), lalu writer."""
        self.stop_web()
        self._host_timer.stop()
        self._sys_timer.stop()
        self.stop_all()
        self._flush_system()
        self.storage.stop()

    # ------------------------------------------------------------ aturan host (Fase 7)
    def _emit_host_alerts(self, cam_id: str, alerts) -> None:
        for a in alerts:
            self.alert_raised.emit(cam_id, record_alert(self.storage, cam_id, a, time.time()))

    def _on_stats(self, cam_id: str, s: dict) -> None:
        self._last_stats[cam_id] = s
        self.web_state.set_stats(cam_id, s)
        self.stats_updated.emit(cam_id, s)

    def _on_error(self, cam_id: str, msg: str) -> None:
        self.web_state.set_error(cam_id, msg)
        if msg.startswith("Modul error") and (eng := self._host_rules.get(cam_id)) is not None:
            self._emit_host_alerts(cam_id, eng.evaluate_host(time.monotonic(), None, True, None))

    def _check_host_rules(self) -> None:
        now = time.monotonic()
        for cam_id in list(self.workers):
            if (eng := self._host_rules.get(cam_id)) is None:
                continue
            s = self._last_stats.get(cam_id)
            state = s.get("state") if s else None
            fps = s.get("proc_fps") if s and state in ("streaming", "file") else None
            self._emit_host_alerts(cam_id, eng.evaluate_host(now, state, False, fps))

    # ------------------------------------------------------------ sumber daya PC
    def _sample_system(self) -> None:
        minute = int(time.time() // 60 * 60)
        if self._sys_minute is not None and minute != self._sys_minute:
            self._flush_system()
        self._sys_minute = minute
        self._sys_samples.append({**self._sampler.sample(), "cams": len(self.workers)})

    def _flush_system(self) -> None:
        xs = self._sys_samples
        if not xs or self._sys_minute is None:
            return

        def avg(key):
            vals = [x[key] for x in xs if x[key] is not None]
            return sum(vals) / len(vals) if vals else None

        self.storage.put(INS_SYSTEM, (self._sys_minute, avg("cpu_pct"), avg("ram_mb"),
                                      avg("gpu_pct"), avg("vram_mb"), max(x["cams"] for x in xs)))
        self._sys_samples = []
