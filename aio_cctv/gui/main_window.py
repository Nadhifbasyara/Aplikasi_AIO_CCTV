"""Jendela utama: sidebar navigasi + QStackedWidget (Fase 5 §2, §4)."""
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.gui.camera_manager import CameraManager
from aio_cctv.gui.pages.alerts import AlertsPage
from aio_cctv.gui.pages.cameras import CamerasPage
from aio_cctv.gui.pages.dashboard import DashboardPage
from aio_cctv.gui.pages.live_view import LiveViewPage
from aio_cctv.gui.pages.modules import ModulesPage
from aio_cctv.gui.pages.reports import ReportsPage
from aio_cctv.gui.pages.settings import SettingsPage
from aio_cctv.gui.pages.zone_editor import ZoneEditorPage
from aio_cctv.gui.utils import app_settings

NAV = ["▶ Live", "Kamera", "Zona", "Modul", "Analitik", "Laporan", "Alert", "Pengaturan"]
ALERT_PAGE = NAV.index("Alert")


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
        self.page_modules = ModulesPage(self.camera_manager)
        self.page_dashboard = DashboardPage(self.camera_manager)
        self.page_reports = ReportsPage(self.camera_manager)
        self.page_alerts = AlertsPage(self.camera_manager)
        self.page_settings = SettingsPage(self.camera_manager)
        for page in (self.page_live, self.page_cameras, self.page_zones, self.page_modules,
                     self.page_dashboard, self.page_reports, self.page_alerts,
                     self.page_settings):
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

        # 4. Alert (Fase 7): tray + jumlah belum dibaca di tombol navigasi
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
            self.tray = QSystemTrayIcon(icon, self)
            self.tray.setToolTip("AIO-CCTV Analytics")
            self.tray.messageClicked.connect(lambda: self._go_to(ALERT_PAGE))
            self.tray.activated.connect(lambda *_: (self.showNormal(), self.activateWindow()))
            self.tray.show()
        self.camera_manager.alert_raised.connect(self._on_alert)
        self.page_alerts.unread_changed.connect(self._set_unread)

    def _on_alert(self, cam_id: str, alert: dict) -> None:
        cam = self.camera_manager.cameras.get(cam_id)
        text = f"[{cam.name if cam else cam_id}] {alert['message']}"
        self.statusBar().showMessage(f"⚠ {text}", 15_000)
        if self.tray is not None and app_settings()["tray_notify"]:
            icon = (QSystemTrayIcon.MessageIcon.Critical if alert["severity"] == "critical"
                    else QSystemTrayIcon.MessageIcon.Warning)
            self.tray.showMessage("Alert AIO-CCTV", text, icon, 10_000)

    def _set_unread(self, n: int) -> None:
        self.nav_group.button(ALERT_PAGE).setText(f"Alert ({n})" if n else "Alert")

    def _edit_zones(self, cam_id: str) -> None:
        self.page_zones.set_camera(cam_id)
        self._go_to(2)

    def _go_to(self, index: int) -> None:
        self.content_area.setCurrentIndex(index)
        self.nav_group.button(index).setChecked(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Wajib: tutup thread & koneksi RTSP sebelum keluar (§5.7)."""
        self.camera_manager.shutdown()
        super().closeEvent(event)
