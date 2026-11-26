"""Dialog tambah/ubah kamera — alur lengkap Fase 5 §5.5.

Semua operasi jaringan (discovery, probe, snapshot) berjalan di QThreadPool
lewat `net_tasks.run_async`, jadi dialog tidak pernah membeku.
"""
import re
import socket

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core import secrets
from aio_cctv.gui import theme
from aio_cctv.gui.camera_manager import (
    CameraConfig,
    CameraManager,
    build_source_uri,
    source_config,
)
from aio_cctv.gui.icons import icon
from aio_cctv.gui.utils import app_settings, friendly_error, to_qimage
from aio_cctv.gui.widgets.module_params import ModuleParamsForm
from aio_cctv.gui.workers.net_tasks import discover_cameras, run_async
from aio_cctv.modules.registry import DEFAULT_MODULE
from aio_cctv.sources.probe import probe
from aio_cctv.sources.snapshot import grab_frame
from aio_cctv.sources.url_builder import BRANDS, has_channels, mask
from aio_cctv.zones.models import SourceConfig

KIND_IP, KIND_FILE, KIND_WEBCAM = 0, 1, 2
VIDEO_FILTER = "Video (*.mp4 *.mkv *.avi *.mov *.webm);;Semua berkas (*)"


def local_cidr() -> str:
    """Tebak subnet /24 dari alamat IP keluar (tidak mengirim paket apa pun)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "192.168.1.1"
    finally:
        s.close()
    return ip.rsplit(".", 1)[0] + ".0/24"


def new_camera_id(name: str, taken) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "kamera"
    cam_id, i = base, 2
    while cam_id in taken:
        cam_id, i = f"{base}_{i}", i + 1
    return cam_id


class DiscoverDialog(QDialog):
    """Langkah 2: cari kamera di jaringan (ONVIF + scan port 554)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cari kamera di jaringan")
        self.resize(460, 340)
        self._busy = False

        self.ed_cidr = QLineEdit(local_cidr())
        self.btn_scan = QPushButton("Cari")
        self.btn_scan.clicked.connect(self._scan)
        self.lst = QListWidget()
        self.lst.itemDoubleClicked.connect(lambda _: self.accept())
        self.lbl = QLabel("Masukkan subnet lalu tekan Cari.")
        self.lbl.setWordWrap(True)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(QLabel("Subnet:"))
        top.addWidget(self.ed_cidr, 1)
        top.addWidget(self.btn_scan)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.lst, 1)
        lay.addWidget(self.lbl)
        lay.addWidget(box)

    def _scan(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_scan.setEnabled(False)
        self.lst.clear()
        self.lbl.setText("Mencari… (ONVIF 3 detik, lalu scan port 554)")
        run_async(discover_cameras, self.ed_cidr.text().strip(),
                  on_done=self._done, on_failed=self._failed)

    def _done(self, found: list) -> None:
        for d in found:
            item = QListWidgetItem(f"{d['host']}   —   {d['via']}")
            item.setData(Qt.ItemDataRole.UserRole, d["host"])
            self.lst.addItem(item)
        self.lbl.setText(f"{len(found)} perangkat ditemukan."
                         if found else "Tidak ada perangkat yang menjawab.")
        self._finish()

    def _failed(self, msg: str) -> None:
        self.lbl.setText(f"Pencarian gagal: {msg}")
        self._finish()

    def _finish(self) -> None:
        self._busy = False
        self.btn_scan.setEnabled(True)

    def selected_host(self) -> str:
        item = self.lst.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""


class AddCameraDialog(QDialog):
    saved = pyqtSignal(str)                        # cam_id yang baru disimpan

    def __init__(self, manager: CameraManager, cam: CameraConfig | None = None, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.editing = cam
        self._alive = True
        self._testing = False
        self._loaded_pw = ""
        self.setWindowTitle("Ubah Kamera" if cam else "Tambah Kamera")
        self._setup_ui()
        if cam:
            self._load(cam)
        self._refresh_url()
        self._fit_to_screen()

    # ------------------------------------------------------------------ UI
    @staticmethod
    def _section(title: str) -> tuple[QFrame, QVBoxLayout, QLabel]:
        """Kartu bagian formulir: judul + isi, seperti desain dialog Tambah Kamera."""
        box = QFrame(objectName="formSection")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        lbl = QLabel(title, objectName="sectionTitle")
        lay.addWidget(lbl)
        return box, lay, lbl

    def _form(self) -> QFormLayout:
        f = QFormLayout()
        self._forms = [*getattr(self, "_forms", []), f]   # untuk menyamakan lebar label
        f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(8)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        return f

    def _setup_ui(self) -> None:
        self.setStyleSheet(
            f"QFrame#formSection {{ background:{theme.SURFACE}; border:1px solid {theme.BORDER};"
            f" border-radius:8px; }}"
            f"QFrame#formSection QLabel {{ border:none; background:transparent; }}"
            f"QLabel#sectionTitle {{ font-size:16px; padding-bottom:2px; }}"
            f"QPushButton#advToggle {{ text-align:left; font-size:15px; padding:9px 12px;"
            f" border:1px solid {theme.BORDER_STRONG}; border-radius:8px;"
            f" background:{theme.SURFACE}; }}"
            f"QPushButton#advToggle:hover {{ background:{theme.SURFACE_2}; }}")
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("mis. Kamera Kasir Depan")
        self.cbo_kind = QComboBox()
        self.cbo_kind.addItems(["Kamera IP (RTSP)", "File video", "Webcam"])
        self.cbo_kind.currentIndexChanged.connect(self._on_kind_changed)

        self.cbo_module = QComboBox()
        self.cbo_module.setToolTip("Modul analitik (model + tracker + analitik) untuk kamera ini")
        for mid, info in sorted(self.manager.modules.items()):
            label = info.manifest.name if info.manifest else mid
            self.cbo_module.addItem(f"{label}  ({mid})" + ("" if info.valid else " — tidak valid"),
                                    mid)
        self.cbo_module.setCurrentIndex(max(0, self.cbo_module.findData(DEFAULT_MODULE)))
        # nama modul panjang tidak boleh melebarkan dialog (isi lalu terpotong di kiri)
        self.cbo_module.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cbo_module.setMinimumContentsLength(18)
        self.cbo_module.currentIndexChanged.connect(self._on_module_changed)
        self.params_form = ModuleParamsForm()

        self.ed_location = QLineEdit()
        self.ed_location.setPlaceholderText("opsional, mis. Lantai 1 / Dekat Kasir")

        # 1. Informasi Dasar
        sec_info, li, _ = self._section("Informasi Dasar")
        head = self._form()
        head.addRow("Nama", self.ed_name)
        head.addRow("Lokasi", self.ed_location)
        head.addRow("Jenis Kamera / Sumber", self.cbo_kind)
        li.addLayout(head)

        # dibuat sebelum halaman sumber: isian sumber langsung memperbarui URL
        self.lbl_url = QLabel("—")
        self.lbl_url.setWordWrap(True)
        self.lbl_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_url.setStyleSheet(f"font-family:monospace; color:{theme.MUTED};")
        self.lbl_url.setToolTip("URL sumber (password tidak pernah ditampilkan)")
        # 2. Koneksi & Jaringan (isi berganti sesuai jenis sumber)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_ip())
        self.stack.addWidget(self._page_file())
        self.stack.addWidget(self._page_webcam())
        sec_conn, lc, self.lbl_conn_title = self._section("Koneksi & Jaringan")
        lc.addWidget(self.stack)

        # 3. Uji Koneksi
        self.btn_test = QPushButton("Tes Koneksi")
        self.btn_test.setMinimumWidth(150)
        self.btn_test.clicked.connect(self._on_test)
        self.lbl_result_icon = QLabel()
        self.lbl_result = QLabel("Belum diuji.")
        self.lbl_result.setWordWrap(True)
        self.lbl_result.setStyleSheet(f"color:{theme.MUTED};")
        self.lbl_snapshot = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.lbl_snapshot.setFixedHeight(200)
        self.lbl_snapshot.setStyleSheet("background:#111; border-radius:6px;")
        self.lbl_snapshot.hide()                   # muncul setelah tes berhasil
        sec_test, lt, _ = self._section("Uji Koneksi")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.btn_test)
        row.addWidget(self.lbl_result_icon)
        row.addWidget(self.lbl_result, 1)
        lt.addLayout(row)
        lt.addWidget(self.lbl_url)
        lt.addWidget(self.lbl_snapshot)

        # 4. Pengaturan Lanjutan (lipat): modul analitik + parameternya
        self.btn_adv = QPushButton("Pengaturan Lanjutan (Opsional)", objectName="advToggle")
        self.btn_adv.setCheckable(True)
        self.btn_adv.setLayoutDirection(Qt.LayoutDirection.RightToLeft)   # ikon panah di kanan
        self.btn_adv.toggled.connect(self._toggle_advanced)
        self.adv = QFrame(objectName="formSection")
        la = QVBoxLayout(self.adv)
        la.setContentsMargins(14, 10, 14, 12)
        fa = self._form()
        fa.addRow("Modul analitik", self.cbo_module)
        la.addLayout(fa)
        lbl_params = QLabel("Parameter modul")
        lbl_params.setStyleSheet(f"color:{theme.MUTED};")
        la.addWidget(lbl_params)
        la.addWidget(self.params_form)
        self._toggle_advanced(False)
        self._on_module_changed()

        # Batal lalu Simpan (biru) di kanan, seperti desain
        self.btn_cancel = QPushButton("Batal")
        self.btn_cancel.setMinimumWidth(80)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton("Simpan")
        self.btn_save.setObjectName("primary")
        self.btn_save.setMinimumWidth(96)
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)
        self.buttons = QHBoxLayout()
        self.buttons.addStretch()
        self.buttons.addWidget(self.btn_cancel)
        self.buttons.addWidget(self.btn_save)

        # isi formulir bisa digulir; tombol Simpan/Batal tetap terlihat di bawah
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(4, 4, 4, 4)
        body.setSpacing(14)
        for w in (sec_info, sec_conn, sec_test, self.btn_adv, self.adv):
            body.addWidget(w)
        body.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addWidget(scroll, 1)
        lay.addLayout(self.buttons)
        self._content = content
        self._align_labels()

    def _align_labels(self) -> None:
        """Lebar kolom label sama di semua bagian, jadi kolom isian berbaris lurus."""
        labels = [f.itemAt(r, QFormLayout.ItemRole.LabelRole).widget()
                  for f in self._forms for r in range(f.rowCount())
                  if f.itemAt(r, QFormLayout.ItemRole.LabelRole) is not None]
        labels = [w for w in labels if w is not None]
        width = max((w.sizeHint().width() for w in labels), default=0)
        for w in labels:
            w.setMinimumWidth(width)

    def _toggle_advanced(self, on: bool) -> None:
        self.adv.setVisible(on)
        self.btn_adv.setIcon(icon("chevron" if on else "chevron_r", theme.TEXT))
        if on and hasattr(self, "_content"):       # perbesar dialog bila layar cukup, lalu
            QTimer.singleShot(0, self._grow_for_advanced)   # gulir ke isian lanjutan

    def _grow_for_advanced(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        max_h = int(screen.availableGeometry().height() * 0.85) if screen else 720
        need = self._content.sizeHint().height() + 120
        if need > self.height():
            self.resize(self.width(), min(need, max_h))
        self._scroll_to(self.adv)

    def _scroll_to(self, w: QWidget) -> None:
        """Gulir VERTIKAL saja (ensureWidgetVisible juga menggeser ke samping)."""
        sa = self.findChild(QScrollArea)
        if sa is None:
            return
        bar = sa.verticalScrollBar()
        bottom = w.mapTo(self._content, w.rect().bottomLeft()).y()
        if bottom > bar.value() + sa.viewport().height():
            bar.setValue(min(bar.maximum(), bottom - sa.viewport().height() + 12))
        sa.horizontalScrollBar().setValue(0)

    def _set_result(self, text: str, state: str = "") -> None:
        """state: ok / warn / bad / "" (netral). Ikon centang/silang seperti desain."""
        color = {"ok": theme.OK, "warn": theme.WARN, "bad": theme.BAD}.get(state, theme.MUTED)
        ico = {"ok": "check_circle", "warn": "warning", "bad": "x_circle"}.get(state)
        self.lbl_result_icon.setPixmap(icon(ico, color).pixmap(18, 18) if ico else QPixmap())
        self.lbl_result_icon.setVisible(bool(ico))
        self.lbl_result.setStyleSheet(f"color:{color};")
        self.lbl_result.setText(text)

    def _manifest(self):
        info = self.manager.modules.get(self.cbo_module.currentData())
        return info.manifest if info is not None and info.valid else None

    def _on_module_changed(self, *_args, values: dict | None = None) -> None:
        self.params_form.set_module(self._manifest(), values)

    def _page_ip(self) -> QWidget:
        self.cbo_brand = QComboBox()
        for key, b in BRANDS.items():
            self.cbo_brand.addItem(b.get("label", key), key)
        self.cbo_brand.currentIndexChanged.connect(self._on_brand_changed)

        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("192.168.1.50")
        self.btn_find = QPushButton("Cari di Jaringan…")
        self.btn_find.clicked.connect(self._on_find)
        host_row = QHBoxLayout()
        host_row.addWidget(self.ed_host, 1)
        host_row.addWidget(self.btn_find)
        host_w = QWidget()
        host_w.setLayout(host_row)
        host_row.setContentsMargins(0, 0, 0, 0)

        self.spin_port = QSpinBox()
        self.spin_port.setRange(0, 65535)
        self.spin_port.setSpecialValueText("bawaan merek")
        self.ed_user = QLineEdit()
        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.cbo_stream = QComboBox()
        self.cbo_stream.addItem("Sub-stream (hemat, untuk analitik)", "sub")
        self.cbo_stream.addItem("Main-stream (resolusi penuh)", "main")
        self.cbo_stream.setCurrentIndex(                  # bawaan dari halaman Pengaturan
            max(0, self.cbo_stream.findData(app_settings()["stream"])))
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("rtsp://192.168.1.50:554/live/ch0")
        self.spin_channel = QSpinBox()                    # DVR/NVR (Fase 9 §6)
        self.spin_channel.setRange(1, 64)
        self.spin_channel.setToolTip("Nomor channel di DVR/NVR. Kamera IP tunggal: biarkan 1. "
                                     "Untuk memasang banyak channel sekaligus pakai "
                                     "“Tambah DVR/NVR” di halaman Kamera.")
        self.spin_channel.valueChanged.connect(self._refresh_url)

        for w in (self.ed_host, self.ed_user, self.ed_pass, self.ed_url):
            w.textChanged.connect(self._refresh_url)
        self.spin_port.valueChanged.connect(self._refresh_url)
        self.cbo_stream.currentIndexChanged.connect(self._refresh_url)

        page = QWidget()
        form = self._form()
        page.setLayout(form)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Merek", self.cbo_brand)
        form.addRow("Alamat IP", host_w)
        form.addRow("Port", self.spin_port)
        form.addRow("Username", self.ed_user)
        form.addRow("Password", self.ed_pass)
        form.addRow("Stream", self.cbo_stream)
        self.row_channel_label = QLabel("Channel DVR")
        form.addRow(self.row_channel_label, self.spin_channel)
        self.row_url_label = QLabel("URL manual")
        form.addRow(self.row_url_label, self.ed_url)
        self._on_brand_changed()
        return page

    def _page_file(self) -> QWidget:
        self.ed_path = QLineEdit()
        self.ed_path.setPlaceholderText("data/videos/ref1.mp4")
        self.ed_path.textChanged.connect(self._refresh_url)
        btn = QPushButton("Pilih berkas…")
        btn.clicked.connect(self._on_browse)
        row = QHBoxLayout()
        row.addWidget(self.ed_path, 1)
        row.addWidget(btn)
        row.setContentsMargins(0, 0, 0, 0)
        row_w = QWidget()
        row_w.setLayout(row)

        page = QWidget()
        form = self._form()
        page.setLayout(form)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Berkas video", row_w)
        form.addRow(QLabel("Video YouTube: unduh dulu dengan yt-dlp (Fase 2), "
                           "lalu pilih berkasnya di sini."))
        return page

    def _page_webcam(self) -> QWidget:
        self.spin_cam = QSpinBox()
        self.spin_cam.setRange(0, 9)
        self.spin_cam.valueChanged.connect(self._refresh_url)
        page = QWidget()
        form = self._form()
        page.setLayout(form)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Indeks perangkat", self.spin_cam)
        return page

    # ------------------------------------------------------- keadaan formulir
    def _on_kind_changed(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self.lbl_conn_title.setText(("Koneksi & Jaringan", "Sumber Video", "Webcam")[idx])
        self._refresh_url()

    def _on_brand_changed(self) -> None:
        custom = self.cbo_brand.currentData() == "custom"
        for w in (self.ed_url, self.row_url_label):
            w.setVisible(custom)
        for w in (self.ed_host, self.btn_find, self.spin_port, self.cbo_stream):
            w.setEnabled(not custom)
        multi = has_channels(self.cbo_brand.currentData())
        for w in (self.spin_channel, self.row_channel_label):
            w.setVisible(multi)
        self._refresh_url()

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Pilih video", "", VIDEO_FILTER)
        if path:
            self.ed_path.setText(path)
            if not self.ed_name.text().strip():
                self.ed_name.setText(path.rsplit("/", 1)[-1].rsplit(".", 1)[0])

    def _on_find(self) -> None:
        dlg = DiscoverDialog(self)
        if dlg.exec() and (host := dlg.selected_host()):
            self.ed_host.setText(host)

    def _config(self) -> CameraConfig:
        """CameraConfig sementara dari isi formulir (belum disimpan)."""
        kind = self.cbo_kind.currentIndex()
        cam_id = self.editing.id if self.editing else "__baru__"
        if kind == KIND_FILE:
            return CameraConfig(cam_id, self.ed_name.text().strip(), "file",
                                file_path=self.ed_path.text().strip())
        if kind == KIND_WEBCAM:
            return CameraConfig(cam_id, self.ed_name.text().strip(), "webcam",
                                file_path=str(self.spin_cam.value()))
        return CameraConfig(
            cam_id, self.ed_name.text().strip(), self.cbo_brand.currentData(),
            host=self.ed_host.text().strip(),
            port=self.spin_port.value() or None,
            user=self.ed_user.text().strip(),
            stream=self.cbo_stream.currentData(),
            url=self.ed_url.text().strip(),
            channel=self.spin_channel.value() if has_channels(self.cbo_brand.currentData()) else 1,
        )

    def _uri(self) -> str:
        return build_source_uri(self._config(), self.ed_pass.text())

    def _refresh_url(self) -> None:
        uri = self._uri()
        cam = self._config()
        if cam.brand not in ("file", "webcam", "custom") and not cam.host:
            uri = ""                               # IP belum diisi: jangan tampilkan rtsp://:554/…
        shown = mask(uri).replace("://:@", "://").replace("://:****@", "://") if uri else "—"
        self.lbl_url.setText(shown)                # password tidak pernah tampil

    # ------------------------------------------------------------ tes koneksi
    def _on_test(self) -> None:
        uri = self._uri()
        if not uri:
            self._set_result("Lengkapi dulu alamat sumbernya.", "warn")
            return
        self._testing = True
        self.btn_test.setEnabled(False)
        self.lbl_snapshot.hide()
        self._set_result("Menguji…")
        if uri.startswith("rtsp://"):
            run_async(probe, uri, on_done=self._on_probe, on_failed=self._on_test_failed)
        else:                                      # file/webcam: langsung ambil snapshot
            self._grab(uri, "Sumber lokal")

    def _on_probe(self, res: dict) -> None:
        if not self._alive:
            return
        if not res.get("ok"):
            self._on_test_failed(friendly_error(res.get("error", "")))
            return
        info = (f"Koneksi berhasil — {res['codec'].upper()} {res['width']}×{res['height']}"
                f" @ {res['fps']} fps")
        self._grab(self._uri(), info)

    def _grab(self, uri: str, info: str) -> None:
        self._set_result(f"{info} — mengambil snapshot…", "ok")
        run_async(grab_frame, uri, on_done=lambda img: self._on_snapshot(img, info),
                  on_failed=self._on_test_failed)

    def _on_snapshot(self, img, info: str) -> None:
        if not self._alive:
            return
        self._testing = False
        self.btn_test.setEnabled(True)
        if img is None:
            self._set_result(f"{info}, tetapi snapshot gagal diambil.", "warn")
            return
        h, w = img.shape[:2]
        pix = QPixmap.fromImage(to_qimage(img)).scaled(
            self.lbl_snapshot.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.lbl_snapshot.setPixmap(pix)
        self.lbl_snapshot.show()
        self._set_result(f"{info} — snapshot {w}×{h} OK", "ok")

    def _on_test_failed(self, msg: str) -> None:
        if not self._alive:
            return
        self._testing = False
        self.btn_test.setEnabled(True)
        self._set_result(friendly_error(msg), "bad")

    # ----------------------------------------------------------------- simpan
    def _validate(self, cam: CameraConfig) -> str:
        if not cam.name:
            return "Nama kamera belum diisi."
        if cam.brand in ("file", "webcam") and not cam.file_path:
            return "Sumber video belum dipilih."
        if cam.brand == "custom" and not cam.url:
            return "URL manual belum diisi."
        if cam.brand not in ("file", "webcam", "custom") and not cam.host:
            return "Alamat IP belum diisi."
        names = {c.name.lower() for c in self.manager.cameras.values()
                 if not self.editing or c.id != self.editing.id}
        if cam.name.lower() in names:
            return f"Nama “{cam.name}” sudah dipakai kamera lain."
        return ""

    def _on_save(self) -> None:
        cam = self._config()
        if err := self._validate(cam):
            QMessageBox.warning(self, "Belum lengkap", err)
            return
        module_id = self.cbo_module.currentData() or DEFAULT_MODULE
        if self._manifest() is None and QMessageBox.question(
                self, "Modul tidak valid",
                f"Modul “{module_id}” tidak terpasang atau tidak valid, sehingga kamera "
                "akan gagal dijalankan. Tetap simpan?") != QMessageBox.StandardButton.Yes:
            return
        if self.editing is None:
            cam.id = new_camera_id(cam.name, self.manager.cameras)
        cam.profile_id = self.editing.profile_id if self.editing and self.editing.profile_id \
            else cam.id
        cam.enabled = self.editing.enabled if self.editing else True
        cam.location = self.ed_location.text().strip()
        if self.editing is not None:               # dialog ini tidak mengubah kelompok DVR
            cam.device = self.editing.device

        if cam.brand not in ("file", "webcam") and self.ed_pass.text() != self._loaded_pw:
            secrets.set_password(cam.id, self.ed_pass.text())   # hanya ke keyring

        # modul & parameter disimpan di profil (Fase 8); zona/aturan yang ada tidak disentuh
        profile = self.manager.profile(cam)
        if profile.module.module_id != module_id:
            profile.module.preset_id = None        # preset milik modul lama
        manifest = self._manifest()
        profile.module.module_id = module_id
        profile.module.module_version = manifest.version if manifest else ""
        profile.module.params = self.params_form.values(manifest)
        profile.source = SourceConfig(**source_config(cam))
        profile.name = profile.name or cam.name

        self.manager.cameras[cam.id] = cam
        self.manager.save_profile(cam, profile)
        self.saved.emit(cam.id)
        self.accept()

    def _load(self, cam: CameraConfig) -> None:
        self.ed_name.setText(cam.name)
        self.ed_location.setText(cam.location)
        binding = self.manager.profile(cam).module
        if self.cbo_module.findData(binding.module_id) < 0:  # modul sudah dicopot
            self.cbo_module.addItem(f"{binding.module_id} — tidak terpasang", binding.module_id)
        self.cbo_module.blockSignals(True)
        self.cbo_module.setCurrentIndex(self.cbo_module.findData(binding.module_id))
        self.cbo_module.blockSignals(False)
        self._on_module_changed(values=binding.params)
        if cam.brand == "file":
            self.cbo_kind.setCurrentIndex(KIND_FILE)
            self.ed_path.setText(cam.file_path)
        elif cam.brand == "webcam":
            self.cbo_kind.setCurrentIndex(KIND_WEBCAM)
            self.spin_cam.setValue(int(cam.file_path or 0))
        else:
            self.cbo_kind.setCurrentIndex(KIND_IP)
            self.cbo_brand.setCurrentIndex(max(0, self.cbo_brand.findData(cam.brand)))
            self.ed_host.setText(cam.host)
            self.spin_port.setValue(cam.port or 0)
            self.ed_user.setText(cam.user)
            self._loaded_pw = secrets.get_password(cam.id)
            self.ed_pass.setText(self._loaded_pw)
            self.cbo_stream.setCurrentIndex(max(0, self.cbo_stream.findData(cam.stream)))
            self.ed_url.setText(cam.url)
            self.spin_channel.setValue(cam.channel or 1)

    def _fit_to_screen(self) -> None:
        """Tinggi = isi formulir, maks. ±85% layar; selebihnya digulir (layar kecil)."""
        screen = (self.parent().screen() if self.parent() else None) or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        h = int(avail.height() * 0.85) if avail else 720
        need = self._content.sizeHint().height() + 120     # isi + tombol & margin
        self.resize(580, min(need, h))

    def done(self, result: int) -> None:
        self._alive = False                        # task yang masih jalan tidak boleh menyentuh UI
        super().done(result)
