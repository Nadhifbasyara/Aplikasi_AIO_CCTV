"""Jendela utama: sidebar navigasi + QStackedWidget (Fase 5 §2, §4)."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.gui.camera_manager import CameraManager
from aio_cctv.gui.pages.cameras import CamerasPage
from aio_cctv.gui.pages.live_view import LiveViewPage
from aio_cctv.gui.pages.settings import SettingsPage
from aio_cctv.gui.pages.zone_editor import ZoneEditorPage

NAV = ["▶ Live", "Kamera", "Zona", "Analitik", "Laporan", "Alert", "Pengaturan"]


def _placeholder(text: str) -> QLabel:
    lbl = QLabel(text, alignment=Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet("color:#888; font-size:14px;")
    return lbl


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AIO-CCTV Analytics")
        self.camera_manager = CameraManager()
        self.setup_ui()
        QTimer.singleShot(0, self.page_live.maybe_autostart)   # setelah jendela tampil

    def setup_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # 1. Sidebar navigasi (kiri)
        sidebar = QVBoxLayout()
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, name in enumerate(NAV):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setMinimumWidth(130)
            self.nav_group.addButton(btn, i)
            sidebar.addWidget(btn)
        sidebar.addStretch()
        main_layout.addLayout(sidebar)

        # 2. Area konten utama (kanan) — urutan harus sama dengan NAV
        self.content_area = QStackedWidget()
        self.page_live = LiveViewPage(self.camera_manager)
        self.page_cameras = CamerasPage(self.camera_manager)
        self.page_zones = ZoneEditorPage(self.camera_manager)
        self.page_settings = SettingsPage(self.camera_manager)
        for page in (self.page_live, self.page_cameras, self.page_zones,
                     _placeholder("Analitik — Fase 6"), _placeholder("Laporan — Fase 6"),
                     _placeholder("Alert — Fase 6"), self.page_settings):
            self.content_area.addWidget(page)
        main_layout.addWidget(self.content_area, stretch=5)

        # 3. Navigasi + status
        self.nav_group.idClicked.connect(self.content_area.setCurrentIndex)
        self.nav_group.button(0).setChecked(True)

        self.lbl_online = QLabel()
        self.statusBar().addPermanentWidget(self.lbl_online)
        self.page_live.online_changed.connect(
            lambda n: self.lbl_online.setText(f"● {n} kamera online"))
        self.page_live.status_changed.connect(self.statusBar().showMessage)
        self.page_live.add_camera_requested.connect(self.page_cameras.add_camera)
        self.page_cameras.zones_requested.connect(self._edit_zones)
        self.lbl_online.setText("● 0 kamera online")

    def _edit_zones(self, cam_id: str) -> None:
        self.page_zones.set_camera(cam_id)
        self._go_to(2)

    def _go_to(self, index: int) -> None:
        self.content_area.setCurrentIndex(index)
        self.nav_group.button(index).setChecked(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Wajib: tutup thread & koneksi RTSP sebelum keluar (§5.7)."""
        self.camera_manager.stop_all()
        super().closeEvent(event)
