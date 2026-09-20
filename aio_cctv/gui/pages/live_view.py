"""Grid multi-kamera dengan overlay analitik (Fase 5 §4)."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.gui.camera_manager import CameraManager
from aio_cctv.gui.utils import app_settings
from aio_cctv.gui.widgets.video_tile import VideoTile

GRIDS = {"1x1": 1, "2x2": 2, "3x3": 3}


class AddCameraTile(QWidget):
    """Kotak '+ Tambah kamera' di slot kosong terakhir (mockup §4)."""

    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        btn = QPushButton("＋  Tambah kamera")
        btn.setMinimumHeight(180)
        btn.setStyleSheet("border:2px dashed #888; color:#888; font-size:14px;")
        btn.clicked.connect(self.clicked)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(btn)


class LiveViewPage(QWidget):
    status_changed = pyqtSignal(str)               # baris status bawah
    online_changed = pyqtSignal(int)               # "● N kamera online"
    add_camera_requested = pyqtSignal()

    def __init__(self, manager: CameraManager):
        super().__init__()
        self.manager = manager
        self.tiles: dict[str, VideoTile] = {}
        self._stats: dict[str, dict] = {}
        self._errors: dict[str, str] = {}
        self.cols = 2
        self._setup_ui()

        manager.cameras_changed.connect(self.rebuild)
        manager.worker_started.connect(self._on_worker_change)
        manager.worker_stopped.connect(self._on_worker_change)
        self.rebuild()

    # ---------------------------------------------------------------- tata letak
    def _setup_ui(self) -> None:
        self.cbo_grid = QComboBox()
        self.cbo_grid.addItems(GRIDS)
        self.cbo_grid.setCurrentText("2x2")
        self.cbo_grid.currentTextChanged.connect(self._on_grid_changed)
        self.btn_start_all = QPushButton("Mulai semua")
        self.btn_stop_all = QPushButton("Hentikan semua")
        self.btn_start_all.clicked.connect(self.start_all)
        self.btn_stop_all.clicked.connect(self.manager.stop_all)

        bar = QHBoxLayout()
        bar.addWidget(self.btn_start_all)
        bar.addWidget(self.btn_stop_all)
        bar.addStretch()
        bar.addWidget(QLabel("Grid:"))
        bar.addWidget(self.cbo_grid)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid_host)

        self.empty_hint = QLabel("Belum ada kamera. Tambahkan lewat halaman Kamera.")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setStyleSheet("color:#888;")

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.empty_hint)
        lay.addWidget(scroll, 1)

    def _on_grid_changed(self, text: str) -> None:
        self.cols = GRIDS.get(text, 2)
        self.rebuild()

    def rebuild(self) -> None:
        """Susun ulang tile sesuai daftar kamera; tile lama dipakai lagi."""
        for i in reversed(range(self.grid.count())):
            self.grid.takeAt(i).widget().setParent(None)

        cams = sorted(self.manager.cameras.values(), key=lambda c: c.name.lower())
        for cam_id in list(self.tiles):
            if cam_id not in self.manager.cameras:           # kamera dihapus
                self.manager.stop(cam_id)
                self.tiles.pop(cam_id).deleteLater()

        for i, cam in enumerate(cams):
            tile = self.tiles.get(cam.id)
            if tile is None:
                tile = VideoTile(cam.id, cam.name)
                tile.toggle_requested.connect(self.toggle)
                self.tiles[cam.id] = tile
            tile.header.setText(cam.name)
            tile.set_running(self.manager.is_running(cam.id))
            if (msg := self._errors.get(cam.id)) is not None:
                tile.set_error(msg)
            self.grid.addWidget(tile, i // self.cols, i % self.cols)

        placeholder = AddCameraTile()
        placeholder.clicked.connect(self.add_camera_requested)
        self.grid.addWidget(placeholder, len(cams) // self.cols, len(cams) % self.cols)
        self.empty_hint.setVisible(not cams)
        self._emit_status()

    # ---------------------------------------------------------------- kendali
    def maybe_autostart(self) -> None:
        if app_settings()["autostart"]:
            self.start_all()

    def start_all(self) -> None:
        for cam in self.manager.cameras.values():
            if cam.enabled:
                self.start(cam.id)

    def start(self, cam_id: str) -> None:
        if self.manager.is_running(cam_id):
            return
        self._errors.pop(cam_id, None)
        self.manager.start(cam_id)                 # sinyal disambung di _on_worker_change

    def toggle(self, cam_id: str) -> None:
        if self.manager.is_running(cam_id):
            self.manager.stop(cam_id)
        else:
            self.start(cam_id)

    # ---------------------------------------------------------------- slot sinyal
    def _on_frame(self, cam_id: str, img: QImage) -> None:
        if (tile := self.tiles.get(cam_id)) is not None:
            tile.set_frame(img)

    def _on_stats(self, cam_id: str, s: dict) -> None:
        self._stats[cam_id] = s
        if (tile := self.tiles.get(cam_id)) is not None:
            tile.set_stats(s)
        self._emit_status()

    def _on_error(self, cam_id: str, msg: str) -> None:
        self._errors[cam_id] = msg
        if (tile := self.tiles.get(cam_id)) is not None:
            tile.set_error(msg)

    def _on_worker_change(self, cam_id: str) -> None:
        running = self.manager.is_running(cam_id)
        if running:
            # setiap start menghasilkan objek worker baru, jadi ini selalu tepat sekali —
            # termasuk saat editor zona menjalankan ulang worker setelah menyimpan
            w = self.manager.workers[cam_id]
            w.frame_ready.connect(self._on_frame)
            w.stats_ready.connect(self._on_stats)
            w.error.connect(self._on_error)
            self._errors.pop(cam_id, None)
        if (tile := self.tiles.get(cam_id)) is not None:
            tile.set_running(running)
            if not running:
                tile.set_idle()
                if (msg := self._errors.get(cam_id)) is not None:
                    tile.set_error(msg)        # jangan sampai pesan error ikut terhapus
        if not running:
            self._stats.pop(cam_id, None)
        self._emit_status()

    def _emit_status(self) -> None:
        running = [c for c in self.manager.cameras if self.manager.is_running(c)]
        self.online_changed.emit(len(running))
        parts = []
        for cam_id in running:
            name = self.manager.cameras[cam_id].name
            s = self._stats.get(cam_id)
            if s is None:
                parts.append(f"{name} memulai")
            elif s["reconnects"]:
                parts.append(f"{name} {s['state']} (#{s['reconnects']})")
            else:
                parts.append(f"{name} {s['state']}")
        self.status_changed.emit(" | ".join(parts) or "Tidak ada kamera berjalan")
