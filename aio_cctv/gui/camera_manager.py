import json
from dataclasses import asdict, dataclass
from urllib.parse import quote, urlsplit, urlunsplit

from PyQt6.QtCore import QObject, pyqtSignal

from aio_cctv.core import secrets
from aio_cctv.core.paths import CAMERAS_FILE, PROFILES_DIR
from aio_cctv.gui.utils import app_settings
from aio_cctv.gui.workers.pipeline_worker import PipelineWorker
from aio_cctv.sources.url_builder import build_url, mask
from aio_cctv.zones.models import Profile, SourceConfig


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
    worker_started = pyqtSignal(str)
    worker_stopped = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.cameras: dict[str, CameraConfig] = {}
        self.workers: dict[str, PipelineWorker] = {}
        self._retired: list[PipelineWorker] = []   # thread yang belum mau berhenti
        self.load()

    def load(self) -> None:
        if CAMERAS_FILE.exists():
            for c in json.loads(CAMERAS_FILE.read_text()):
                self.cameras[c["id"]] = CameraConfig(**c)

    def save(self) -> None:
        CAMERAS_FILE.write_text(json.dumps([asdict(c) for c in self.cameras.values()], indent=2))
        self.cameras_changed.emit()

    def source_uri(self, cam: CameraConfig) -> str:
        pw = secrets.get_password(cam.id)          # dari OS keyring, bukan dari file
        return build_source_uri(cam, pw)

    def _load_profile(self, cam: CameraConfig) -> Profile:
        """Profil kamera; kamera yang zonanya belum digambar tetap boleh jalan."""
        path = PROFILES_DIR / f"{cam.profile_id}.json"
        if cam.profile_id and path.exists():
            return Profile.load(path)
        return Profile(                            # tanpa zona/garis: deteksi + tracking saja
            profile_id=cam.profile_id or cam.id,
            source=SourceConfig(**source_config(cam)),
        )

    def is_running(self, cam_id: str) -> bool:
        return cam_id in self.workers

    def start(self, cam_id: str, settings: dict | None = None) -> PipelineWorker:
        if (running := self.workers.get(cam_id)) is not None:
            return running
        cam = self.cameras[cam_id]
        w = PipelineWorker(cam_id, self.source_uri(cam), self._load_profile(cam),
                           settings or app_settings())
        w.finished.connect(lambda cid=cam_id, worker=w: self._on_worker_finished(cid, worker))
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
