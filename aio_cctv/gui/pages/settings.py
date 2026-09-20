"""Halaman Pengaturan (Fase 5 §5.8). Semua nilai disimpan dengan QSettings."""
import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core.paths import MODELS_DIR
from aio_cctv.gui.utils import DEFAULTS, app_settings, qsettings

log = logging.getLogger(__name__)

MODELS = [("yolo11n", "yolo11n — paling ringan"),
          ("yolo11s", "yolo11s — seimbang"),
          ("yolo11m", "yolo11m — paling akurat")]
STREAMS = [("sub", "Sub-stream — hemat, disarankan untuk analitik"),
           ("main", "Main-stream — resolusi penuh")]


def _devices() -> list[tuple[str, str]]:
    out = [("auto", "auto — pilih sendiri"), ("cpu", "cpu")]
    try:
        import torch
        for i in range(torch.cuda.device_count()):
            out.append((f"cuda:{i}", f"cuda:{i} — {torch.cuda.get_device_name(i)}"))
    except Exception as e:                         # torch belum ada / driver bermasalah
        log.info("daftar GPU tidak tersedia: %s", e)
    return out


class SettingsPage(QWidget):
    settings_saved = pyqtSignal()

    def __init__(self, camera_manager=None):
        super().__init__()
        self.camera_manager = camera_manager
        self._setup_ui()
        self.load()

    def _setup_ui(self) -> None:
        self.cbo_model = QComboBox()
        for key, label in MODELS:
            self.cbo_model.addItem(label, key)
        self.spin_conf = QDoubleSpinBox()
        self.spin_conf.setRange(0.05, 0.95)
        self.spin_conf.setSingleStep(0.05)
        self.spin_conf.setDecimals(2)
        self.cbo_device = QComboBox()
        for key, label in _devices():
            self.cbo_device.addItem(label, key)
        self.spin_grace = QDoubleSpinBox()
        self.spin_grace.setRange(0.0, 30.0)
        self.spin_grace.setSingleStep(0.5)
        self.spin_grace.setSuffix(" detik")

        analitik = QGroupBox("Analitik")
        f1 = QFormLayout(analitik)
        f1.addRow("Model", self.cbo_model)
        f1.addRow("Confidence", self.spin_conf)
        f1.addRow("Device", self.cbo_device)
        f1.addRow("Grace period dwell", self.spin_grace)
        self.lbl_weights = QLabel()
        self.lbl_weights.setStyleSheet("color:#888;")
        f1.addRow("", self.lbl_weights)
        self.cbo_model.currentIndexChanged.connect(self._show_weights)

        self.cbo_stream = QComboBox()
        for key, label in STREAMS:
            self.cbo_stream.addItem(label, key)
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(1, 60)
        self.spin_fps.setSuffix(" fps")
        self.chk_realtime = QCheckBox("Putar file video sesuai FPS aslinya")
        self.chk_realtime.setToolTip(
            "Tanpa ini file diputar secepat inferensi, bukan seperti kamera live.")
        self.chk_autostart = QCheckBox("Jalankan kamera aktif saat aplikasi dibuka")

        tampilan = QGroupBox("Sumber & tampilan")
        f2 = QFormLayout(tampilan)
        f2.addRow("Stream untuk analitik", self.cbo_stream)
        f2.addRow("FPS tampilan maks", self.spin_fps)
        f2.addRow("", self.chk_realtime)
        f2.addRow("", self.chk_autostart)
        f2.addRow("", QLabel("Stream dipakai sebagai nilai awal saat menambah kamera baru."))

        self.cbo_lang = QComboBox()
        self.cbo_lang.addItem("Indonesia", "id")
        self.cbo_lang.setEnabled(False)
        self.cbo_lang.setToolTip("Terjemahan antarmuka belum tersedia (rencana Fase 10).")
        umum = QGroupBox("Umum")
        f3 = QFormLayout(umum)
        f3.addRow("Bahasa", self.cbo_lang)

        self.btn_save = QPushButton("Simpan pengaturan")
        self.btn_reset = QPushButton("Kembalikan ke bawaan")
        self.btn_save.clicked.connect(self.save)
        self.btn_reset.clicked.connect(self.reset)
        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)

        bar = QHBoxLayout()
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_reset)
        bar.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(analitik)
        lay.addWidget(tampilan)
        lay.addWidget(umum)
        lay.addLayout(bar)
        lay.addWidget(self.lbl_status)
        lay.addStretch()

    def _show_weights(self) -> None:
        path = MODELS_DIR / f"{self.cbo_model.currentData()}.pt"
        self.lbl_weights.setText(
            str(path) if path.exists() else f"{path} — belum diunduh, akan diambil saat dipakai")

    # ------------------------------------------------------------ nilai
    def load(self, values: dict | None = None) -> None:
        s = values or app_settings()
        self.cbo_model.setCurrentIndex(max(0, self.cbo_model.findData(s["model"])))
        self.spin_conf.setValue(s["conf"])
        self.cbo_device.setCurrentIndex(max(0, self.cbo_device.findData(s["device"])))
        self.spin_grace.setValue(s["dwell_grace"])
        self.cbo_stream.setCurrentIndex(max(0, self.cbo_stream.findData(s["stream"])))
        self.spin_fps.setValue(s["display_fps"])
        self.chk_realtime.setChecked(s["realtime_file"])
        self.chk_autostart.setChecked(s["autostart"])
        self.cbo_lang.setCurrentIndex(max(0, self.cbo_lang.findData(s["language"])))
        self._show_weights()

    def values(self) -> dict:
        return {
            "model": self.cbo_model.currentData(),
            "conf": round(self.spin_conf.value(), 2),
            "device": self.cbo_device.currentData(),
            "dwell_grace": round(self.spin_grace.value(), 2),
            "stream": self.cbo_stream.currentData(),
            "display_fps": self.spin_fps.value(),
            "realtime_file": self.chk_realtime.isChecked(),
            "autostart": self.chk_autostart.isChecked(),
            "language": self.cbo_lang.currentData(),
        }

    def save(self) -> None:
        s = qsettings()
        for key, value in self.values().items():
            s.setValue(key, value)
        s.sync()
        self.settings_saved.emit()
        note = ""
        if self.camera_manager is not None and self.camera_manager.workers:
            running = list(self.camera_manager.workers)
            for cam_id in running:                 # model/conf/device baru butuh worker baru
                self.camera_manager.stop(cam_id)
                self.camera_manager.start(cam_id)
            note = f" {len(running)} kamera dijalankan ulang agar pengaturan baru dipakai."
        self.lbl_status.setStyleSheet("color:#2E7D32;")
        self.lbl_status.setText(f"Pengaturan tersimpan.{note}")

    def reset(self) -> None:
        if QMessageBox.question(
                self, "Kembalikan ke bawaan",
                "Kembalikan semua pengaturan ke nilai bawaan?"
        ) != QMessageBox.StandardButton.Yes:
            return
        s = qsettings()
        for key in DEFAULTS:
            s.remove(key)
        s.sync()
        self.load()
        self.lbl_status.setStyleSheet("color:#888;")
        self.lbl_status.setText("Pengaturan dikembalikan ke bawaan (belum diterapkan ke "
                                "kamera yang berjalan — tekan Simpan).")
