"""Dialog tambah/ubah kamera — alur lengkap Fase 5 §5.5.

Semua operasi jaringan (discovery, probe, snapshot) berjalan di QThreadPool
lewat `net_tasks.run_async`, jadi dialog tidak pernah membeku.
"""
import re
import socket

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core import secrets
from aio_cctv.core.paths import PROFILES_DIR
from aio_cctv.gui.camera_manager import (
    CameraConfig,
    CameraManager,
    build_source_uri,
    source_config,
)
from aio_cctv.gui.utils import app_settings, friendly_error, to_qimage
from aio_cctv.gui.workers.net_tasks import discover_cameras, run_async
from aio_cctv.sources.probe import probe
from aio_cctv.sources.snapshot import grab_frame
from aio_cctv.sources.url_builder import BRANDS, mask
from aio_cctv.zones.models import Profile, SourceConfig

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
        self.setWindowTitle("Ubah kamera" if cam else "Tambah kamera")
        self.resize(640, 620)
        self._setup_ui()
        if cam:
            self._load(cam)
        self._refresh_url()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self) -> None:
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("mis. Kasir depan")
        self.cbo_kind = QComboBox()
        self.cbo_kind.addItems(["Kamera IP (RTSP)", "File video", "Webcam"])
        self.cbo_kind.currentIndexChanged.connect(self._on_kind_changed)

        head = QFormLayout()
        head.addRow("Nama", self.ed_name)
        head.addRow("Jenis sumber", self.cbo_kind)

        self.lbl_url = QLabel("—")
        self.lbl_url.setWordWrap(True)
        self.lbl_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_url.setStyleSheet("font-family:monospace; color:#555;")

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_ip())
        self.stack.addWidget(self._page_file())
        self.stack.addWidget(self._page_webcam())

        self.btn_test = QPushButton("Tes Koneksi")
        self.btn_test.clicked.connect(self._on_test)
        self.lbl_result = QLabel("Belum diuji.")
        self.lbl_result.setWordWrap(True)
        self.lbl_snapshot = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.lbl_snapshot.setFixedHeight(200)
        self.lbl_snapshot.setStyleSheet("background:#111; color:#888;")
        self.lbl_snapshot.setText("snapshot muncul di sini")

        test_box = QGroupBox("Tes koneksi")
        tl = QVBoxLayout(test_box)
        tl.addWidget(QLabel("URL sumber:"))
        tl.addWidget(self.lbl_url)
        tl.addWidget(self.btn_test)
        tl.addWidget(self.lbl_result)
        tl.addWidget(self.lbl_snapshot)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Simpan")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Batal")
        self.buttons.accepted.connect(self._on_save)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(self.stack)
        lay.addWidget(test_box, 1)
        lay.addWidget(self.buttons)

    def _page_ip(self) -> QWidget:
        self.cbo_brand = QComboBox()
        for key, b in BRANDS.items():
            self.cbo_brand.addItem(b.get("label", key), key)
        self.cbo_brand.currentIndexChanged.connect(self._on_brand_changed)

        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("192.168.1.50")
        self.btn_find = QPushButton("Cari di jaringan…")
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

        for w in (self.ed_host, self.ed_user, self.ed_pass, self.ed_url):
            w.textChanged.connect(self._refresh_url)
        self.spin_port.valueChanged.connect(self._refresh_url)
        self.cbo_stream.currentIndexChanged.connect(self._refresh_url)

        page = QWidget()
        form = QFormLayout(page)
        form.addRow("Merek", self.cbo_brand)
        form.addRow("Alamat IP", host_w)
        form.addRow("Port", self.spin_port)
        form.addRow("Username", self.ed_user)
        form.addRow("Password", self.ed_pass)
        form.addRow("Stream", self.cbo_stream)
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
        form = QFormLayout(page)
        form.addRow("Berkas video", row_w)
        form.addRow(QLabel("Video YouTube: unduh dulu dengan yt-dlp (Fase 2), "
                           "lalu pilih berkasnya di sini."))
        return page

    def _page_webcam(self) -> QWidget:
        self.spin_cam = QSpinBox()
        self.spin_cam.setRange(0, 9)
        self.spin_cam.valueChanged.connect(self._refresh_url)
        page = QWidget()
        form = QFormLayout(page)
        form.addRow("Indeks perangkat", self.spin_cam)
        return page

    # ------------------------------------------------------- keadaan formulir
    def _on_kind_changed(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self._refresh_url()

    def _on_brand_changed(self) -> None:
        custom = self.cbo_brand.currentData() == "custom"
        for w in (self.ed_url, self.row_url_label):
            w.setVisible(custom)
        for w in (self.ed_host, self.btn_find, self.spin_port, self.cbo_stream):
            w.setEnabled(not custom)
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
        )

    def _uri(self) -> str:
        return build_source_uri(self._config(), self.ed_pass.text())

    def _refresh_url(self) -> None:
        uri = self._uri()
        self.lbl_url.setText(mask(uri) if uri else "—")      # password tidak pernah tampil

    # ------------------------------------------------------------ tes koneksi
    def _on_test(self) -> None:
        uri = self._uri()
        if not uri:
            self.lbl_result.setText("Lengkapi dulu alamat sumbernya.")
            return
        self._testing = True
        self.btn_test.setEnabled(False)
        self.lbl_result.setStyleSheet("")
        self.lbl_result.setText("Menguji…")
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
        info = (f"Terhubung — {res['codec'].upper()} {res['width']}×{res['height']}"
                f" @ {res['fps']} fps")
        self._grab(self._uri(), info)

    def _grab(self, uri: str, info: str) -> None:
        self.lbl_result.setStyleSheet("color:#2E7D32;")
        self.lbl_result.setText(f"{info} — mengambil snapshot…")
        run_async(grab_frame, uri, on_done=lambda img: self._on_snapshot(img, info),
                  on_failed=self._on_test_failed)

    def _on_snapshot(self, img, info: str) -> None:
        if not self._alive:
            return
        self._testing = False
        self.btn_test.setEnabled(True)
        if img is None:
            self.lbl_result.setStyleSheet("color:#EF6C00;")
            self.lbl_result.setText(f"{info}, tetapi snapshot gagal diambil.")
            return
        h, w = img.shape[:2]
        pix = QPixmap.fromImage(to_qimage(img)).scaled(
            self.lbl_snapshot.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.lbl_snapshot.setPixmap(pix)
        self.lbl_result.setStyleSheet("color:#2E7D32;")
        self.lbl_result.setText(f"{info} — snapshot {w}×{h} OK")

    def _on_test_failed(self, msg: str) -> None:
        if not self._alive:
            return
        self._testing = False
        self.btn_test.setEnabled(True)
        self.lbl_result.setStyleSheet("color:#C62828;")
        self.lbl_result.setText(friendly_error(msg))

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
        if self.editing is None:
            cam.id = new_camera_id(cam.name, self.manager.cameras)
        cam.profile_id = cam.id

        if cam.brand not in ("file", "webcam") and self.ed_pass.text() != self._loaded_pw:
            secrets.set_password(cam.id, self.ed_pass.text())   # hanya ke keyring

        profile_path = PROFILES_DIR / f"{cam.id}.json"
        if not profile_path.exists():              # profil kosong, zona digambar di halaman Zona
            Profile(profile_id=cam.id, source=SourceConfig(**source_config(cam))).save(profile_path)

        self.manager.cameras[cam.id] = cam
        self.manager.save()
        self.saved.emit(cam.id)
        self.accept()

    def _load(self, cam: CameraConfig) -> None:
        self.ed_name.setText(cam.name)
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

    def done(self, result: int) -> None:
        self._alive = False                        # task yang masih jalan tidak boleh menyentuh UI
        super().done(result)
