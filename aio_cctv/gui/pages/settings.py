"""Halaman Pengaturan (Fase 5 §5.8). Semua nilai disimpan dengan QSettings."""
import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from aio_cctv.gui.utils import DEFAULTS, app_settings, qsettings

log = logging.getLogger(__name__)

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
        self.cbo_device = QComboBox()
        for key, label in _devices():
            self.cbo_device.addItem(label, key)

        analitik = QGroupBox("Inferensi")
        f1 = QFormLayout(analitik)
        f1.addRow("Device", self.cbo_device)
        note = QLabel("Model, kelas, confidence, dan parameter analitik ditentukan modul "
                      "yang dipilih per kamera (halaman Modul & dialog kamera).")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        f1.addRow("", note)

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

        tampilan = QGroupBox("Sumber && tampilan")   # "&&": "&" tunggal = shortcut Qt
        f2 = QFormLayout(tampilan)
        f2.addRow("Stream untuk analitik", self.cbo_stream)
        f2.addRow("FPS tampilan maks", self.spin_fps)
        f2.addRow("", self.chk_realtime)
        f2.addRow("", self.chk_autostart)
        f2.addRow("", QLabel("Stream dipakai sebagai nilai awal saat menambah kamera baru."))

        self.chk_blur = QCheckBox("Buramkan orang di tampilan live (mode anonim)")
        self.chk_blur.setToolTip("Kotak setiap orang diburamkan sebelum frame ditampilkan. "
                                 "Analitik tetap berjalan normal.")
        self.spin_retention = QSpinBox()
        self.spin_retention.setRange(0, 3650)
        self.spin_retention.setSuffix(" hari")
        self.spin_retention.setSpecialValueText("selamanya")
        self.spin_retention.setToolTip("Event, metrik, dan alert yang lebih tua dihapus saat "
                                       "aplikasi dibuka. Video tidak pernah disimpan.")
        self.chk_tray = QCheckBox("Tampilkan notifikasi alert di tray sistem")
        privasi = QGroupBox("Data && privasi")
        f4 = QFormLayout(privasi)
        f4.addRow("", self.chk_blur)
        f4.addRow("Simpan data analitik", self.spin_retention)
        f4.addRow("", self.chk_tray)

        self.chk_web = QCheckBox("Aktifkan dashboard web lokal")
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1024, 65535)
        self.chk_lan = QCheckBox("Izinkan akses dari HP/PC lain di jaringan lokal (wajib login)")
        self.chk_lan.setToolTip("Mati = hanya bisa dibuka dari PC ini (127.0.0.1). Jangan "
                                "port-forward ke internet.")
        self.btn_web_pw = QPushButton("Atur kata sandi web…")
        self.btn_web_pw.clicked.connect(self._set_web_password)
        self.btn_web_open = QPushButton("Buka di browser")
        self.btn_web_open.clicked.connect(self._open_web)
        self.lbl_web = QLabel()
        self.lbl_web.setWordWrap(True)
        self.lbl_web.setStyleSheet("color:#888;")
        web = QGroupBox("Dashboard web (localhost)")
        f5 = QFormLayout(web)
        f5.addRow("", self.chk_web)
        f5.addRow("Port", self.spin_port)
        f5.addRow("", self.chk_lan)
        web_btns = QHBoxLayout()
        web_btns.addWidget(self.btn_web_pw)
        web_btns.addWidget(self.btn_web_open)
        web_btns.addStretch()
        f5.addRow("", web_btns)
        f5.addRow("", self.lbl_web)

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
        lay.addWidget(privasi)
        lay.addWidget(web)
        lay.addWidget(umum)
        lay.addLayout(bar)
        lay.addWidget(self.lbl_status)
        lay.addStretch()

    # ------------------------------------------------------------ nilai
    def load(self, values: dict | None = None) -> None:
        s = values or app_settings()
        self.cbo_device.setCurrentIndex(max(0, self.cbo_device.findData(s["device"])))
        self.cbo_stream.setCurrentIndex(max(0, self.cbo_stream.findData(s["stream"])))
        self.spin_fps.setValue(s["display_fps"])
        self.chk_realtime.setChecked(s["realtime_file"])
        self.chk_autostart.setChecked(s["autostart"])
        self.chk_blur.setChecked(s["blur"])
        self.spin_retention.setValue(s["retention_days"])
        self.chk_tray.setChecked(s["tray_notify"])
        self.chk_web.setChecked(s["web_enabled"])
        self.spin_port.setValue(s["web_port"])
        self.chk_lan.setChecked(s["web_lan"])
        self._show_web_status()
        self.cbo_lang.setCurrentIndex(max(0, self.cbo_lang.findData(s["language"])))

    def values(self) -> dict:
        return {
            "device": self.cbo_device.currentData(),
            "stream": self.cbo_stream.currentData(),
            "display_fps": self.spin_fps.value(),
            "realtime_file": self.chk_realtime.isChecked(),
            "autostart": self.chk_autostart.isChecked(),
            "blur": self.chk_blur.isChecked(),
            "retention_days": self.spin_retention.value(),
            "tray_notify": self.chk_tray.isChecked(),
            "web_enabled": self.chk_web.isChecked(),
            "web_port": self.spin_port.value(),
            "web_lan": self.chk_lan.isChecked(),
            "language": self.cbo_lang.currentData(),
        }

    # ------------------------------------------------------------ dashboard web
    def _show_web_status(self) -> None:
        from aio_cctv.web.server import lan_addresses

        web = getattr(self.camera_manager, "web", None)
        if web is None:
            err = getattr(self.camera_manager, "web_error", "")
            self.lbl_web.setText(f"Tidak berjalan{': ' + err if err else ''}.")
            return
        lan = ", ".join(f"http://{ip}:{web.port}" for ip in lan_addresses()) if web.allow_lan \
            else "mati (hanya PC ini)"
        self.lbl_web.setText(f"Berjalan di {web.url} · akses LAN: {lan}")

    def _open_web(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(f"http://localhost:{self.spin_port.value()}"))

    def _set_web_password(self) -> bool:
        from PyQt6.QtWidgets import QInputDialog, QLineEdit

        from aio_cctv.web.auth import set_web_password

        pw, ok = QInputDialog.getText(self, "Kata sandi dashboard web",
                                      "Kata sandi baru (min. 8 karakter):", QLineEdit.EchoMode.Password)
        if not ok:
            return False
        if len(pw) < 8:
            QMessageBox.warning(self, "Kata sandi", "Minimal 8 karakter.")
            return False
        again, ok = QInputDialog.getText(self, "Kata sandi dashboard web", "Ulangi kata sandi:",
                                         QLineEdit.EchoMode.Password)
        if not ok or again != pw:
            QMessageBox.warning(self, "Kata sandi", "Kata sandi tidak sama.")
            return False
        try:
            set_web_password(pw)                   # hanya hash yang disimpan, di OS keyring
        except Exception as e:
            QMessageBox.warning(self, "Kata sandi", f"Gagal menyimpan ke keyring: {e}")
            return False
        QMessageBox.information(self, "Kata sandi", "Kata sandi tersimpan. Berlaku setelah "
                                "pengaturan disimpan.")
        return True

    def save(self) -> None:
        from aio_cctv.web.auth import web_password_hash

        if self.chk_lan.isChecked() and self.chk_web.isChecked() and not web_password_hash():
            QMessageBox.information(self, "Akses LAN", "Akses dari jaringan lokal butuh kata "
                                    "sandi. Atur kata sandi dulu.")
            if not self._set_web_password():
                return
        s = qsettings()
        for key, value in self.values().items():
            s.setValue(key, value)
        s.sync()
        self.settings_saved.emit()
        note = ""
        if self.camera_manager is not None and self.camera_manager.workers:
            running = list(self.camera_manager.workers)
            for cam_id in running:                 # device baru butuh worker baru
                self.camera_manager.stop(cam_id)
                self.camera_manager.start(cam_id)
            note = f" {len(running)} kamera dijalankan ulang agar pengaturan baru dipakai."
        self.lbl_status.setStyleSheet("color:#2E7D32;")
        self.lbl_status.setText(f"Pengaturan tersimpan.{note}")
        self._show_web_status()

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
