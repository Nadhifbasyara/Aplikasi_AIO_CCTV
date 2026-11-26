"""Halaman Pengaturan (Fase 5 §5.8). Semua nilai disimpan dengan QSettings."""
import logging

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.gui import theme
from aio_cctv.gui.icons import icon
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


def _switch(text: str) -> QCheckBox:
    c = QCheckBox(text)
    c.setObjectName("switch")
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    return c


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color:{theme.MUTED};")
    return lbl


def _field(label: str, widget, hint: str = "") -> QWidget:
    """Label di atas isian + keterangan abu-abu di bawahnya (gaya mockup)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    lbl = QLabel(label)
    lbl.setObjectName("fieldLabel")
    lay.addWidget(lbl)
    if isinstance(widget, QWidget):
        lay.addWidget(widget)
    else:
        lay.addLayout(widget)
    if hint:
        lay.addWidget(_hint(hint))
    return w


def _card(title: str, ico: str, items: list) -> QFrame:
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(12)
    head = QHBoxLayout()
    head.setSpacing(10)
    pic = QLabel()
    pic.setPixmap(icon(ico, theme.PRIMARY).pixmap(24, 24))
    head.addWidget(pic)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    head.addWidget(t, 1)
    lay.addLayout(head)
    for it in items:
        lay.addWidget(it)
    lay.addStretch()
    return card


class _Row(QWidget):
    """Dua kolom berdampingan; ditumpuk bila halaman sempit."""

    NARROW = 860

    def __init__(self, *widgets):
        super().__init__()
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(16)
        for w in widgets:
            # lebar dibagi rata persis (tidak ikut lebar minimum isi) agar kolom antarbaris sejajar
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.box.addWidget(w, 1)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        want = (QBoxLayout.Direction.TopToBottom if e.size().width() < self.NARROW
                else QBoxLayout.Direction.LeftToRight)
        if self.box.direction() != want:
            self.box.setDirection(want)


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
        self.cbo_lang = QComboBox()
        self.cbo_lang.addItem("Indonesia", "id")
        self.cbo_lang.setEnabled(False)
        self.cbo_lang.setToolTip("Terjemahan antarmuka belum tersedia (rencana Fase 10).")

        self.cbo_stream = QComboBox()
        for key, label in STREAMS:
            self.cbo_stream.addItem(label, key)
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(1, 60)
        self.spin_fps.setSuffix(" fps")
        self.chk_realtime = _switch("Putar file video sesuai FPS aslinya")
        self.chk_realtime.setToolTip(
            "Tanpa ini file diputar secepat inferensi, bukan seperti kamera live.")
        self.chk_autostart = _switch("Jalankan kamera aktif saat aplikasi dibuka")

        self.chk_blur = _switch("Buramkan orang di tampilan live (mode anonim)")
        self.chk_blur.setToolTip("Kotak setiap orang diburamkan sebelum frame ditampilkan. "
                                 "Analitik tetap berjalan normal.")
        self.spin_retention = QSpinBox()
        self.spin_retention.setRange(0, 3650)
        self.spin_retention.setSpecialValueText("selamanya")
        self.spin_retention.setToolTip("Event, metrik, dan alert yang lebih tua dihapus saat "
                                       "aplikasi dibuka. Video tidak pernah disimpan.")
        self.chk_tray = _switch("Tampilkan notifikasi alert di tray sistem")

        self.chk_web = _switch("Aktifkan dashboard web lokal")
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1024, 65535)
        self.spin_port.setMinimumWidth(110)
        self.chk_lan = _switch("Izinkan akses dari HP/PC lain di jaringan lokal (wajib login)")
        self.chk_lan.setToolTip("Mati = hanya bisa dibuka dari PC ini (127.0.0.1). Jangan "
                                "port-forward ke internet.")
        self.btn_web_pw = QPushButton(icon("key", theme.TEXT), " Atur Kata Sandi…")
        self.btn_web_pw.clicked.connect(self._set_web_password)
        self.btn_web_open = QPushButton(icon("external", "#FFFFFF"), " Buka di Browser")
        self.btn_web_open.setObjectName("primary")
        self.btn_web_open.clicked.connect(self._open_web)
        self.lbl_web = QLabel()
        self.lbl_web.setWordWrap(True)
        self.lbl_web.setOpenExternalLinks(True)
        self.lbl_web.setTextFormat(Qt.TextFormat.RichText)

        self.chk_shared = _switch("Satu model bersama untuk semua kamera (batching)")
        self.chk_shared.setToolTip("Kamera yang memakai modul sama berbagi satu model YOLO dan "
                                   "dideteksi bersama dalam satu batch. Hemat VRAM dan menaikkan "
                                   "jumlah kamera yang sanggup dilayani.")
        self.spin_batch = QSpinBox()
        self.spin_batch.setRange(1, 32)
        self.spin_batch.setSuffix(" frame")
        self.spin_target = QSpinBox()
        self.spin_target.setRange(0, 60)
        self.spin_target.setSuffix(" fps")
        self.spin_target.setSpecialValueText("ikuti kamera")
        self.spin_target.setToolTip("Analisis kamera RTSP dibatasi ke FPS ini; frame di antaranya "
                                    "dilewati sebelum deteksi. Jangan di bawah FPS minimum tracker "
                                    "modul (umumnya 8).")
        self.cbo_backend = QComboBox()
        self.cbo_backend.addItem("Python (Ultralytics, PyTorch)", "python")
        self.cbo_backend.addItem("NVIDIA DeepStream (NVDEC + TensorRT FP16, Docker)", "deepstream")
        self.cbo_backend.setToolTip(
            "DeepStream: decode dan deteksi kamera RTSP dikerjakan GPU di container DeepStream, "
            "dibatch bersama; tracker & analitik tetap modul rekan. Butuh Docker + image "
            "DeepStream 9.1. Bila tidak tersedia, kamera otomatis memakai Python.")
        self.cbo_backend.currentIndexChanged.connect(self._backend_changed)

        # ---------------------------------------------------------- kartu
        sumber = _card("Sumber & Tampilan CCTV", "camera", [
            _field("Aliran Video untuk Analitik (Stream)", self.cbo_stream,
                   "Dipakai sebagai nilai awal saat menambah kamera baru."),
            _field("Maksimal FPS Tampilan", self.spin_fps),
            self.chk_realtime, self.chk_autostart])
        inferensi = _card("Inference & Deteksi AI", "cpu", [
            _field("Pilih Device Analitik", self.cbo_device,
                   "Sistem otomatis memilih perangkat terbaik (GPU/CPU) untuk memproses "
                   "analitik. Model, kelas, dan confidence ditentukan modul per kamera."),
            _field("Bahasa Antarmuka", self.cbo_lang)])
        retention = QHBoxLayout()
        retention.setSpacing(0)
        retention.addWidget(self.spin_retention, 1)
        unit = QLabel("Hari")
        unit.setObjectName("unitAddon")
        retention.addWidget(unit)
        privasi = _card("Data & Privasi", "shield", [
            _field("Samarkan Wajah / Orang", self.chk_blur),
            _field("Masa Simpan Data", retention,
                   "Event, metrik, dan alert yang lebih lama dihapus otomatis. Video tidak "
                   "pernah disimpan."),
            _field("Notifikasi Sistem", self.chk_tray)])
        port = QHBoxLayout()
        port.setSpacing(8)
        port.addWidget(self.spin_port)
        port.addWidget(self.btn_web_pw)
        port.addWidget(self.btn_web_open)
        port.addStretch()
        web = _card("Akses Dashboard Web", "globe", [
            self.chk_web, _field("Port Akses", port), self.chk_lan, self.lbl_web])
        lanjutan = _card("Backend & Batch Deteksi", "module", [
            _field("Backend Deteksi", self.cbo_backend,
                   "DeepStream memakai decoder & TensorRT di GPU; bila tidak tersedia kamera "
                   "otomatis memakai Python."),
            self.chk_shared,
            _field("Ukuran Batch Maks", self.spin_batch)])
        optim = _card("Beban Analisis Kamera", "gauge", [
            _field("FPS Analisis Maks", self.spin_target,
                   "Frame di antaranya dilewati sebelum deteksi. Jangan di bawah FPS minimum "
                   "tracker modul (umumnya 8)."),
            _hint("Tile Live yang tidak terlihat (pindah halaman, jendela diminimalkan) tidak "
                  "digambar ulang, jadi tenaga PC dipakai untuk analisis.")])

        # ---------------------------------------------------------- isi bergulir
        content = QWidget()
        content.setObjectName("settingsContent")
        body = QVBoxLayout(content)
        body.setContentsMargins(18, 14, 18, 18)
        body.setSpacing(10)
        title = QLabel("Pengaturan Sistem")
        title.setObjectName("settingsTitle")
        body.addWidget(title)
        body.addWidget(_hint("Sesuaikan konfigurasi aplikasi untuk performa terbaik."))
        body.addSpacing(6)
        umum = self._section("Umum, Sumber & Tampilan")
        body.addWidget(umum)
        body.addWidget(_Row(sumber, inferensi))
        body.addSpacing(8)
        body.addWidget(_Row(self._column("Data & Privasi", privasi),
                            self._column("Dashboard Web (Localhost)", web)))
        body.addSpacing(8)
        body.addWidget(_Row(self._column("Analitik & AI (Lanjutan)", lanjutan),
                            self._column("Optimasi Kamera", optim)))
        body.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(content)

        # ---------------------------------------------------------- tombol bawah
        self.btn_save = QPushButton(icon("save", "#FFFFFF"), " Simpan Pengaturan")
        self.btn_save.setObjectName("primary")
        self.btn_reset = QPushButton(icon("undo", theme.BAD), " Kembalikan Default")
        self.btn_reset.setObjectName("danger")
        for b in (self.btn_save, self.btn_reset, self.btn_web_pw, self.btn_web_open):
            b.setIconSize(QSize(16, 16))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setMinimumHeight(34)
        self.btn_reset.setMinimumHeight(34)
        self.btn_save.clicked.connect(self.save)
        self.btn_reset.clicked.connect(self.reset)
        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        footer = QFrame()
        footer.setObjectName("settingsFooter")
        bar = QHBoxLayout(footer)
        bar.setContentsMargins(18, 8, 18, 8)
        bar.addWidget(self.lbl_status, 1)
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_reset)

        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(self.scroll, 1)
        right.addWidget(footer)                  # tombol Simpan selalu terlihat
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(right, 1)
        self.setStyleSheet(
            "QLabel#settingsTitle { font-size:22px; }"
            "QLabel#settingsSection { font-size:18px; }"
            "QLabel#fieldLabel { font-weight:600; }"
            f"QLabel#unitAddon {{ background:{theme.SURFACE_2}; border:1px solid {theme.BORDER}; "
            "border-left:none; border-top-right-radius:4px; border-bottom-right-radius:4px; "
            "padding:0 12px; }"
            f"QFrame#settingsFooter {{ border:none; border-top:1px solid {theme.BORDER}; }}")

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("settingsSection")
        return lbl

    def _column(self, title: str, card: QWidget) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(self._section(title))
        lay.addWidget(card, 1)
        return w

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
        self.chk_shared.setChecked(s["inference"] == "shared")
        self.spin_batch.setValue(s["max_batch"])
        self.spin_target.setValue(s["target_fps"])
        self.cbo_backend.setCurrentIndex(max(0, self.cbo_backend.findData(s["backend"])))
        self._backend_changed()

    def _backend_changed(self) -> None:
        python = self.cbo_backend.currentData() == "python"
        self.chk_shared.setEnabled(python)         # opsi Jalur A saja
        self.spin_batch.setEnabled(python)

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
            "inference": "shared" if self.chk_shared.isChecked() else "per_camera",
            "max_batch": self.spin_batch.value(),
            "target_fps": self.spin_target.value(),
            "backend": self.cbo_backend.currentData(),
        }

    # ------------------------------------------------------------ dashboard web
    def _show_web_status(self) -> None:
        from aio_cctv.web.server import lan_addresses

        web = getattr(self.camera_manager, "web", None)
        if web is None:
            err = getattr(self.camera_manager, "web_error", "")
            self.lbl_web.setText(f"<span style='color:{theme.MUTED}'>Status:</span> "
                                 f"Tidak berjalan{': ' + err if err else ''}.")
            return
        lan = ", ".join(f"<a href='http://{ip}:{web.port}'>http://{ip}:{web.port}</a>"
                        for ip in lan_addresses()) if web.allow_lan \
            else "Tidak Aktif (hanya di PC ini)"
        self.lbl_web.setText(f"Berjalan di: <a href='{web.url}'>{web.url}</a><br>"
                             f"Akses LAN: {lan}")

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
        self.lbl_status.setStyleSheet(f"color:{theme.OK};")
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
        self.lbl_status.setStyleSheet(f"color:{theme.MUTED};")
        self.lbl_status.setText("Pengaturan dikembalikan ke bawaan (belum diterapkan ke "
                                "kamera yang berjalan — tekan Simpan).")
