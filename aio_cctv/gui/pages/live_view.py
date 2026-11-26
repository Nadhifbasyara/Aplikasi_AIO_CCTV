"""Grid multi-kamera dengan overlay analitik (Fase 5 §4) + panel Aktivitas Terbaru."""
from datetime import datetime

import psutil
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.gui import theme
from aio_cctv.gui.camera_manager import CameraManager
from aio_cctv.gui.utils import app_settings
from aio_cctv.gui.widgets.activity_log import ActivityLog, describe_event
from aio_cctv.gui.widgets.video_tile import VideoTile

GRIDS = {"1x1": 1, "2x2": 2, "3x3": 3}


SEVERITY_COLOR = {"critical": theme.BAD, "warning": theme.WARN}


def _action_style(color: str, hover: str, text: str = "white") -> str:
    """Tombol aksi berwarna; versi redup saat nonaktif agar tetap terbaca."""
    return (f"QPushButton {{ background:{color}; color:{text}; border:none; border-radius:7px;"
            f" padding:7px 16px; font-size:14px; }}"
            f"QPushButton:hover {{ background:{hover}; }}"
            f"QPushButton:disabled {{ background:#5A5A5A; color:#B5B5B5; }}")


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
        self._describe: dict[str, tuple[dict, dict]] = {}   # cam_id -> (label event, nama zona)
        self._focus: str | None = None                       # kamera yang diperbesar
        self.cols = 2
        self._setup_ui()
        manager.alert_raised.connect(self._on_alert)

        manager.cameras_changed.connect(self.rebuild)
        manager.worker_started.connect(self._on_worker_change)
        manager.worker_stopped.connect(self._on_worker_change)
        self.rebuild()
        # Fase 9 §5.5 render hemat: worker hanya menganotasi frame untuk tile yang terlihat
        self._vis_timer = QTimer(self, interval=500)
        self._vis_timer.timeout.connect(self._sync_visibility)
        self._vis_timer.start()
        psutil.cpu_percent(None)                   # panggilan pertama selalu 0
        self._clock_timer = QTimer(self, interval=2000)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start()
        self._tick()

    def _sync_visibility(self) -> None:
        win = self.window()
        shown = win.isVisible() and not win.isMinimized()
        for cam_id, w in list(self.manager.workers.items()):
            tile = self.tiles.get(cam_id)
            w.gui_visible = bool(shown and tile is not None and tile.isVisible())

    # ---------------------------------------------------------------- tata letak
    def _setup_ui(self) -> None:
        self.cbo_grid = QComboBox()
        self.cbo_grid.addItems(GRIDS)
        self.cbo_grid.setCurrentText("2x2")
        self.cbo_grid.setToolTip("Tata letak grid kamera")
        self.cbo_grid.currentTextChanged.connect(self._on_grid_changed)
        self.btn_start_all = QPushButton("▶  Mulai semua")
        self.btn_stop_all = QPushButton("■  Hentikan semua")
        self.btn_start_all.setStyleSheet(_action_style("#62D9AB", "#4CC797", "#0E2A1F"))  # mint
        self.btn_stop_all.setStyleSheet(_action_style("#E5484D", "#C93B40"))                # merah
        self.btn_start_all.clicked.connect(self.start_all)
        self.btn_stop_all.clicked.connect(self.manager.stop_all)
        self.lbl_clock = QLabel()
        self.lbl_clock.setStyleSheet(f"color:{theme.MUTED};")
        self.lbl_dot = QLabel("●")

        bar = QHBoxLayout()
        bar.addWidget(self.btn_start_all)
        bar.addWidget(self.btn_stop_all)
        bar.addStretch()
        bar.addWidget(self.cbo_grid)
        bar.addSpacing(14)
        bar.addWidget(self.lbl_clock)
        bar.addWidget(self.lbl_dot)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.grid_host)

        self.empty_hint = QWidget()
        hint_lay = QVBoxLayout(self.empty_hint)
        hint = QLabel("Belum ada kamera.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color:#888;")
        btn_add = QPushButton("＋  Tambah kamera")
        btn_add.clicked.connect(self.add_camera_requested)
        hint_lay.addStretch()
        hint_lay.addWidget(hint)
        hint_lay.addWidget(btn_add, 0, Qt.AlignmentFlag.AlignHCenter)
        hint_lay.addStretch()

        self.activity = ActivityLog()
        self.activity.setFixedWidth(300)

        left = QVBoxLayout()
        left.addWidget(self.empty_hint)
        left.addWidget(scroll, 1)
        body = QHBoxLayout()
        body.setSpacing(12)
        body.addLayout(left, 1)
        body.addWidget(self.activity)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addLayout(bar)
        lay.addLayout(body, 1)

    def _tick(self) -> None:
        """Jam + CPU PC di kanan atas; titik = kesehatan kamera yang berjalan."""
        cpu = psutil.cpu_percent(None)
        self.lbl_clock.setText(f"{datetime.now().astimezone():%b %d %H:%M}  |  CPU {cpu:.0f}%")
        running = [c for c in self.manager.cameras if self.manager.is_running(c)]
        bad = [c for c in running if self._errors.get(c) or
               (self._stats.get(c) or {}).get("state") in ("reconnecting", "connecting")]
        color = "#757575" if not running else theme.WARN if bad else theme.OK
        self.lbl_dot.setStyleSheet(f"color:{color};")
        self.lbl_dot.setToolTip("Tidak ada kamera berjalan" if not running else
                                f"{len(bad)} kamera bermasalah" if bad else
                                "Semua kamera normal")

    def _on_grid_changed(self, text: str) -> None:
        self.cols = GRIDS.get(text, 2)
        self.rebuild()

    def rebuild(self) -> None:
        """Susun ulang tile sesuai daftar kamera; tile lama dipakai lagi."""
        for i in reversed(range(self.grid.count())):
            self.grid.takeAt(i).widget().setParent(None)

        cams = sorted(self.manager.cameras.values(), key=lambda c: c.name.lower())
        if self._focus not in self.manager.cameras:
            self._focus = None
        for cam_id in list(self.tiles):
            if cam_id not in self.manager.cameras:           # kamera dihapus
                self.manager.stop(cam_id)
                self.tiles.pop(cam_id).deleteLater()

        for i, cam in enumerate(cams):
            tile = self.tiles.get(cam.id)
            if tile is None:
                tile = VideoTile(cam.id, cam.name)
                tile.toggle_requested.connect(self.toggle)
                tile.focus_requested.connect(self.toggle_focus)
                self.tiles[cam.id] = tile
            tile.set_title(cam.name)
            tile.set_running(self.manager.is_running(cam.id))
            tile.set_focused(self._focus == cam.id)
            if (msg := self._errors.get(cam.id)) is not None:
                tile.set_error(msg)
        shown = [c for c in cams if self._focus in (None, c.id)]
        cols = 1 if self._focus else self.cols
        for i, cam in enumerate(shown):
            self.grid.addWidget(self.tiles[cam.id], i // cols, i % cols)

        self.empty_hint.setVisible(not cams)
        self._emit_status()

    # ---------------------------------------------------------------- kendali
    def toggle_focus(self, cam_id: str) -> None:
        """Tombol ⛶: tampilkan satu kamera memenuhi area; klik lagi untuk kembali ke grid."""
        self._focus = None if self._focus == cam_id else cam_id
        self.cbo_grid.setEnabled(self._focus is None)
        self.rebuild()

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
        self.activity.add(self._prefix(cam_id) + msg, color=theme.BAD)

    # ---------------------------------------------------------------- aktivitas
    def _labels_for(self, cam_id: str) -> tuple[dict, dict]:
        """Label event (manifest modul) & nama zona/garis (profil) untuk kalimat log."""
        cam = self.manager.cameras.get(cam_id)
        if cam is None:
            return {}, {}
        profile = self.manager.profile(cam)
        info = self.manager.modules.get(profile.module.module_id)
        labels = {e.type: e.label for e in info.manifest.events} \
            if info is not None and info.valid else {}
        regions = {r.id: r.name for r in [*profile.zones, *profile.lines]}
        return labels, regions

    def _prefix(self, cam_id: str) -> str:
        cam = self.manager.cameras.get(cam_id)
        return f"{cam.name}: " if cam is not None and len(self.manager.cameras) > 1 else ""

    def _on_result(self, cam_id: str, res) -> None:
        if not res.events:
            return
        labels, regions = self._describe.get(cam_id) or self._labels_for(cam_id)
        for e in res.events:
            self.activity.add(self._prefix(cam_id) + describe_event(e, labels, regions))

    def _on_alert(self, cam_id: str, alert: dict) -> None:
        self.activity.add(self._prefix(cam_id) + "⚠ " + alert.get("message", "Alert"),
                          alert.get("ts"), SEVERITY_COLOR.get(alert.get("severity"), theme.WARN))

    def _on_worker_change(self, cam_id: str) -> None:
        running = self.manager.is_running(cam_id)
        if running:
            # setiap start menghasilkan objek worker baru, jadi ini selalu tepat sekali —
            # termasuk saat editor zona menjalankan ulang worker setelah menyimpan
            w = self.manager.workers[cam_id]
            w.frame_ready.connect(self._on_frame)
            w.stats_ready.connect(self._on_stats)
            w.error.connect(self._on_error)
            w.result_ready.connect(self._on_result)
            self._errors.pop(cam_id, None)
            self._describe[cam_id] = self._labels_for(cam_id)
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
        startable = [c for c in self.manager.cameras.values()
                     if c.enabled and not self.manager.is_running(c.id)]
        self.btn_start_all.setEnabled(bool(startable))
        self.btn_stop_all.setEnabled(bool(running))
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
