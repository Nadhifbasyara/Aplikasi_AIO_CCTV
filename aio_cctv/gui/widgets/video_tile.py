from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

STATE_COLOR = {"streaming": "#43A047", "file": "#1E88E5", "connecting": "#FB8C00",
               "reconnecting": "#E53935", "stopped": "#757575"}


class VideoTile(QWidget):
    toggle_requested = pyqtSignal(str)             # cam_id: minta start/stop

    def __init__(self, cam_id: str, title: str):
        super().__init__()
        self.cam_id = cam_id
        self.title = title
        self.header = QLabel(title)
        self.btn_toggle = QToolButton()
        self.btn_toggle.setAutoRaise(True)
        self.btn_toggle.clicked.connect(lambda: self.toggle_requested.emit(self.cam_id))
        self.view = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.view.setMinimumHeight(180)
        self.view.setStyleSheet("background:#111; color:#888;")
        self.footer = QLabel("—")
        self.footer.setWordWrap(True)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.header, 1)
        top.addWidget(self.btn_toggle)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addLayout(top)
        for w in (self.view, self.footer):
            lay.addWidget(w)
        lay.setStretch(1, 1)
        self.set_running(False)
        self.set_idle()

    def _paint_header(self, state: str) -> None:
        color = STATE_COLOR.get(state, "#757575")
        self.header.setStyleSheet(f"border-left:6px solid {color}; padding-left:6px;")

    def set_running(self, running: bool) -> None:
        self.btn_toggle.setText("■" if running else "▶")
        self.btn_toggle.setToolTip("Hentikan kamera" if running else "Jalankan kamera")

    def set_idle(self) -> None:
        self._paint_header("stopped")
        self.view.setPixmap(QPixmap())
        self.view.setText("berhenti")
        self.footer.setStyleSheet("")
        self.footer.setText("—")

    def set_frame(self, img: QImage) -> None:
        pix = QPixmap.fromImage(img).scaled(self.view.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
        self.view.setPixmap(pix)

    def set_stats(self, s: dict) -> None:
        self._paint_header(s["state"])
        width, height = s.get("resolution", (0, 0))
        text = (
            f"Stream type: {s.get('stream_type', '-')}\n"
            f"Resolution: {width}x{height} | FPS: {s.get('stream_fps', 0.0):.1f}\n"
            f"Bandwidth: {s.get('bandwidth_kbps', 0.0):.2f} kBps\n"
            f"Total frame: {s.get('total_frames', 0)}\n"
            f"Decoded frame: {s.get('decoded_frames', 0)}\n"
            f"Dropped frame rate: {s.get('dropped_rate', 0.0):.2f}%\n"
            f"Modul: {s.get('module', '-')} | Track: {s.get('tracks', 0)}\n"
            f"Detect: {s['detect_ms']:.0f} ms | Modul: {s.get('module_ms', 0.0):.0f} ms"
            f" | Process: {s['proc_fps']:.1f} fps"
        )
        for label, value in (s.get("metrics") or {}).items():   # label & satuan dari manifest
            text += f"\n{label}: {value}"
        self.footer.setStyleSheet("")
        self.footer.setText(text)

    def set_error(self, msg: str) -> None:
        self._paint_header("reconnecting")
        self.footer.setStyleSheet("color:#E53935;")
        self.footer.setText(msg)
        self.footer.setToolTip(msg)
