"""Daftar kamera + tambah/ubah/hapus (Fase 5 §3, §5.5)."""
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core import secrets
from aio_cctv.core.paths import PROFILES_DIR
from aio_cctv.gui.camera_manager import CameraConfig, CameraManager, source_config
from aio_cctv.gui.dialogs.add_camera import AddCameraDialog
from aio_cctv.sources.url_builder import BRANDS
from aio_cctv.zones.models import Profile

log = logging.getLogger(__name__)
COLS = ["Nama", "Jenis", "Alamat", "Stream", "Zona/Garis", "Status", "Aktif"]


class CamerasPage(QWidget):
    zones_requested = pyqtSignal(str)              # cam_id: buka halaman Zona

    def __init__(self, manager: CameraManager):
        super().__init__()
        self.manager = manager
        self._setup_ui()
        manager.cameras_changed.connect(self.refresh)
        manager.worker_started.connect(lambda _: self.refresh())
        manager.worker_stopped.connect(lambda _: self.refresh())
        self.refresh()

    def _setup_ui(self) -> None:
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.doubleClicked.connect(self.edit_camera)

        self.btn_add = QPushButton("Tambah kamera…")
        self.btn_edit = QPushButton("Ubah")
        self.btn_del = QPushButton("Hapus")
        self.btn_zones = QPushButton("Gambar zona")
        self.btn_add.clicked.connect(self.add_camera)
        self.btn_edit.clicked.connect(self.edit_camera)
        self.btn_del.clicked.connect(self.delete_camera)
        self.btn_zones.clicked.connect(self._request_zones)

        bar = QHBoxLayout()
        bar.addWidget(self.btn_add)
        bar.addWidget(self.btn_edit)
        bar.addWidget(self.btn_del)
        bar.addWidget(self.btn_zones)
        bar.addStretch()

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.table, 1)

    # ------------------------------------------------------------------ isi
    def _kind_label(self, cam: CameraConfig) -> str:
        if cam.brand == "file":
            return "File video"
        if cam.brand == "webcam":
            return "Webcam"
        return BRANDS.get(cam.brand, {}).get("label", cam.brand)

    def _address(self, cam: CameraConfig) -> str:
        # source_config() sudah ter-mask dan tidak menyentuh keyring — refresh tabel
        # tidak boleh memicu permintaan unlock keyring untuk tiap baris
        return source_config(cam)["uri"]

    def _zone_summary(self, cam: CameraConfig) -> str:
        path = PROFILES_DIR / f"{cam.profile_id or cam.id}.json"
        if not path.exists():
            return "belum ada"
        try:
            p = Profile.load(path)
        except Exception as e:                     # profil rusak: jangan jatuhkan halaman
            log.warning("profil %s tidak terbaca: %s", path.name, e)
            return "rusak"
        return f"{len(p.zones)} zona / {len(p.lines)} garis"

    def refresh(self) -> None:
        self.table.blockSignals(True)              # hindari _on_item_changed saat mengisi
        cams = sorted(self.manager.cameras.values(), key=lambda c: c.name.lower())
        self.table.setRowCount(len(cams))
        for r, cam in enumerate(cams):
            values = [cam.name, self._kind_label(cam), self._address(cam),
                      "—" if cam.brand in ("file", "webcam") else cam.stream,
                      self._zone_summary(cam),
                      "berjalan" if self.manager.is_running(cam.id) else "berhenti"]
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, cam.id)
                self.table.setItem(r, c, item)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if cam.enabled else Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, cam.id)
            self.table.setItem(r, len(COLS) - 1, chk)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.blockSignals(False)
        has = bool(cams)
        for b in (self.btn_edit, self.btn_del, self.btn_zones):
            b.setEnabled(has)

    # --------------------------------------------------------------- aksi
    def selected_id(self) -> str:
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != len(COLS) - 1:
            return
        cam = self.manager.cameras.get(item.data(Qt.ItemDataRole.UserRole))
        if cam is None:
            return
        cam.enabled = item.checkState() == Qt.CheckState.Checked
        if not cam.enabled:
            self.manager.stop(cam.id)
        self.manager.save()

    def add_camera(self) -> None:
        dlg = AddCameraDialog(self.manager, parent=self)
        dlg.saved.connect(self.zones_requested)    # §5.5 langkah 5: lanjut menggambar zona
        dlg.exec()

    def edit_camera(self) -> None:
        if not (cam := self.manager.cameras.get(self.selected_id())):
            return
        was_running = self.manager.is_running(cam.id)
        dlg = AddCameraDialog(self.manager, cam=cam, parent=self)
        if dlg.exec() and was_running:
            self.manager.stop(cam.id)              # pakai pengaturan baru
            self.manager.start(cam.id)

    def delete_camera(self) -> None:
        if not (cam := self.manager.cameras.get(self.selected_id())):
            return
        if QMessageBox.question(
                self, "Hapus kamera",
                f"Hapus “{cam.name}”? Profil zonanya tetap disimpan.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.manager.stop(cam.id)
        try:
            import keyring
            keyring.delete_password(secrets.SERVICE, cam.id)
        except Exception as e:                     # tidak ada entri / keyring terkunci
            log.info("password %s tidak dihapus: %s", cam.id, e)
        self.manager.cameras.pop(cam.id, None)
        self.manager.save()

    def _request_zones(self) -> None:
        if cam_id := self.selected_id():
            self.zones_requested.emit(cam_id)
