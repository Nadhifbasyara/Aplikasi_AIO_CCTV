from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from aio_cctv.gui import theme

STATE_COLOR = {"streaming": "#43A047", "file": "#1E88E5", "connecting": "#FB8C00",
               "reconnecting": "#E53935", "stopped": "#757575"}


def fmt_bitrate(kbps: float | None) -> str:
    """Satuan seperti di pengaturan kamera CCTV: Kbps / Mbps (kilobit, bukan kilobyte)."""
    if kbps is None:
        return "— (tidak terukur di DeepStream)"
    return f"{kbps / 1000:.2f} Mbps" if kbps >= 1000 else f"{kbps:.0f} Kbps"


def stats_text(s: dict) -> str:
    """Teks statistik tile. Baris "Kamera" = apa yang dikirim kamera (sama dengan pengaturan
    stream di web/NVR kamera); baris "Analisis" = apa yang benar-benar diproses aplikasi."""
    sw, sh = s.get("source_resolution") or s.get("resolution", (0, 0))
    aw, ah = s.get("resolution", (0, 0))
    codec = f" | {s['codec']}" if s.get("codec") else ""
    target = s.get("target_fps") or 0
    is_file = s.get("stream_type") == "File"
    lines = [
        f"Stream: {s.get('stream_type', '-')}{codec}",
        f"Kamera: {sw}x{sh} @ {s.get('stream_fps', 0.0):.1f} fps",
    ]
    if not is_file:
        lines.append(f"Bitrate: {fmt_bitrate(s.get('bitrate_kbps'))}")
    res = "" if (aw, ah) == (sw, sh) else f"{aw}x{ah} @ "
    tgt = f" (target {target:.0f})" if target and not is_file else ""
    lines += [
        f"Analisis: {res}{s.get('proc_fps', 0.0):.1f} fps{tgt}",
        f"Frame: diterima {s.get('total_frames', 0)} · dianalisis {s.get('decoded_frames', 0)}",
        f"Dilewati: {s.get('skip_pct', 0.0):.0f}% (sengaja) · Hilang: {s.get('lost_pct', 0.0):.1f}%",
        f"Modul: {s.get('module', '-')} | Track: {s.get('tracks', 0)}",
        f"Detect: {s.get('detect_ms', 0.0):.0f} ms | Modul: {s.get('module_ms', 0.0):.0f} ms",
    ]
    for label, value in (s.get("metrics") or {}).items():   # label & satuan dari manifest
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


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
        self.footer.setStyleSheet("")
        self.footer.setText(stats_text(s))

    def set_error(self, msg: str) -> None:
        self._paint_header("reconnecting")
        self.footer.setStyleSheet(f"color:{theme.BAD};")
        self.footer.setText(msg)
        self.footer.setToolTip(msg)
