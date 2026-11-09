import cv2
import numpy as np
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QImage

ORG, APP = "tugasakhir", "aio-cctv"

# Nilai bawaan halaman Pengaturan (Fase 5 §5.8). Model, confidence, dan parameter analitik
# (mis. grace period) sejak Fase 6 ditentukan modul per kamera, bukan pengaturan global.
DEFAULTS = {
    "stream": "sub",
    "display_fps": 15,
    "device": "auto",
    "language": "id",
    "autostart": True,
    "realtime_file": True,       # putar file sesuai FPS asli (demo terlihat seperti kamera live)
    "blur": False,               # mode anonim tampilan (Fase 7 §3.7)
    "retention_days": 90,        # 0 = simpan selamanya
    "tray_notify": True,         # notifikasi alert di tray
    "web_enabled": True,         # dashboard web lokal (Fase 8 §8)
    "web_port": 8080,
    "web_lan": False,            # False = hanya 127.0.0.1; True = LAN + wajib login
}
_TYPES = {"stream": str, "display_fps": int, "device": str, "language": str,
          "autostart": bool, "realtime_file": bool, "blur": bool, "retention_days": int,
          "tray_notify": bool, "web_enabled": bool, "web_port": int, "web_lan": bool}


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
