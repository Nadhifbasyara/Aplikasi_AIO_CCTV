import cv2
import numpy as np
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QImage

from aio_cctv.core.paths import MODELS_DIR

ORG, APP = "tugasakhir", "aio-cctv"

# Nilai bawaan halaman Pengaturan (Fase 5 §5.8)
DEFAULTS = {
    "model": "yolo11n",
    "conf": 0.35,
    "stream": "sub",
    "display_fps": 15,
    "device": "auto",
    "dwell_grace": 1.5,
    "language": "id",
    "autostart": True,
    "realtime_file": True,       # putar file sesuai FPS asli (demo terlihat seperti kamera live)
}
_TYPES = {"model": str, "conf": float, "stream": str, "display_fps": int,
          "device": str, "dwell_grace": float, "language": str, "autostart": bool,
          "realtime_file": bool}


def to_qimage(bgr: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    # .copy() wajib: buffer numpy akan hilang setelah fungsi selesai
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def qsettings() -> QSettings:
    """QSettings dengan org/app eksplisit agar tidak bergantung pada QApplication."""
    return QSettings(ORG, APP)


def app_settings() -> dict:
    """Pengaturan aplikasi + turunannya (dipakai PipelineWorker)."""
    s = qsettings()
    out = {k: s.value(k, v, type=_TYPES[k]) for k, v in DEFAULTS.items()}
    out["display_fps"] = max(1, out["display_fps"])        # cegah bagi nol di worker
    out["weights"] = str(MODELS_DIR / f"{out['model']}.pt")
    return out


# Pesan ffprobe -> bahasa pengguna (tabel Fase 4 §5.4)
_ERRORS = [
    ("401", "Username/password salah — Tapo: pakai Camera Account, EZVIZ: verification code"),
    ("Unauthorized",
     "Username/password salah — Tapo: pakai Camera Account, EZVIZ: verification code"),
    ("404", "Path stream salah — coba template merek lain"),
    ("Not found", "Path stream salah — coba template merek lain"),
    ("Invalid data", "Path stream salah — coba template merek lain"),
    ("Connection refused", "Port RTSP tertutup / RTSP belum diaktifkan di aplikasi kamera"),
    ("timeout", "Tidak ada jawaban — IP salah, kamera beda jaringan, atau firewall"),
    ("No route to host", "Tidak ada jawaban — IP salah, kamera beda jaringan, atau firewall"),
]


def friendly_error(raw: str) -> str:
    low = (raw or "").lower()
    for needle, msg in _ERRORS:
        if needle.lower() in low:
            return msg
    return (raw or "Gagal terhubung").strip()[:200]
