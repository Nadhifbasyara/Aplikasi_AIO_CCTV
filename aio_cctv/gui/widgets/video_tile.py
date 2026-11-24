"""Tile kamera di Live View + panel "Info stream".

Mengikuti pola VMS pada umumnya: tile hanya menampilkan ringkasan 2 baris (info stream kamera
dan analitik) plus peringatan yang muncul HANYA bila ada masalah; rincian lengkap ada di panel
Info stream (tombol ⓘ). Waktu per tahap (deteksi/modul) tetap tercatat untuk benchmark, tetapi
hanya tampil di bagian "Teknis" panel.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.gui import theme

STATE_COLOR = {"streaming": "#43A047", "file": "#1E88E5", "connecting": "#FB8C00",
               "reconnecting": "#E53935", "stopped": "#757575"}
STATE_LABEL = {"streaming": "Online", "file": "Memutar file", "connecting": "Menyambung…",
               "reconnecting": "Menyambung ulang", "stopped": "Berhenti"}

# Ambang peringatan di tile
LOST_WARN_PCT = 5.0          # frame hilang karena beban (bukan yang dilewati sengaja)
LATENCY_WARN_MS = 500.0      # sama dengan kriteria QoS Fase 9 §2
RECONNECT_WARN_10M = 3       # tersambung ulang ≥ 3× dalam 10 menit terakhir


# ---------------------------------------------------------------- format
def fmt_bitrate(kbps: float | None) -> str:
    """Satuan seperti di pengaturan kamera CCTV: Kbps / Mbps (kilobit, bukan kilobyte)."""
    if kbps is None:
        return "— (tidak terukur di DeepStream)"
    return f"{kbps / 1000:.2f} Mbps" if kbps >= 1000 else f"{kbps:.0f} Kbps"


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} hari {h} jam"
    if h:
        return f"{h} jam {m} menit"
    return f"{m} menit {s} detik" if m else f"{s} detik"


def fmt_codec(codec: str) -> str:
    """Nama codec seperti di pengaturan kamera (FFmpeg: h264/hevc → H.264/H.265)."""
    c = (codec or "").upper()
    return {"H264": "H.264", "HEVC": "H.265", "H265": "H.265", "MJPEG": "MJPEG"}.get(c, c)


def _res(wh) -> str:
    w, h = wh or (0, 0)
    return f"{w}×{h}" if w else "—"


def _is_file(s: dict) -> bool:
    return s.get("stream_type") == "File"


# ---------------------------------------------------------------- isi tile
def summary_lines(s: dict) -> list[str]:
    """2 baris tile: (1) stream dari kamera, seperti di pengaturan kamera; (2) analitik."""
    src = s.get("source_resolution") or s.get("resolution")
    parts = [_res(src), f"{s.get('stream_fps', 0.0):.0f} fps"]
    if s.get("codec"):
        parts.append(fmt_codec(s["codec"]))
    if _is_file(s):
        parts.insert(0, "File")
    elif s.get("bitrate_kbps") is not None:
        parts.append(fmt_bitrate(s["bitrate_kbps"]))
    if s.get("stream_label"):
        parts.append(s["stream_label"].replace("-stream", ""))
    line2 = [f"Analisis {s.get('proc_fps', 0.0):.0f} fps"]
    line2 += [f"{k}: {v}" for k, v in list((s.get("metrics") or {}).items())[:2]]
    return [" · ".join(parts), " · ".join(line2)]


def warnings(s: dict) -> list[str]:
    """Peringatan yang perlu dilihat operator; kosong = semua normal."""
    out = []
    if s.get("lost_pct", 0.0) > LOST_WARN_PCT:
        out.append(f"Frame hilang {s['lost_pct']:.0f}% — PC kewalahan")
    if not _is_file(s) and s.get("latency_p95_ms", 0.0) > LATENCY_WARN_MS:
        out.append(f"Latensi {s['latency_p95_ms']:.0f} ms")
    if s.get("reconnects_10m", 0) >= RECONNECT_WARN_10M:
        out.append(f"Tersambung ulang {s['reconnects_10m']}× dalam 10 menit")
    return out


# ---------------------------------------------------------------- isi panel
def detail_sections(s: dict) -> list[tuple[str, list[tuple[str, str]]]]:
    """Rincian untuk panel Info stream, dikelompokkan seperti VMS."""
    file = _is_file(s)
    target = s.get("target_fps") or 0
    camera = [("Resolusi", _res(s.get("source_resolution") or s.get("resolution"))),
              ("FPS", f"{s.get('stream_fps', 0.0):.1f}"),
              ("Codec", fmt_codec(s.get("codec", "")) or "—")]
    if not file:
        camera += [("Bitrate", fmt_bitrate(s.get("bitrate_kbps"))),
                   ("Stream", s.get("stream_label") or "— (URL manual)"),
                   ("Transport", s.get("transport", "RTSP/TCP"))]
    conn = [("Status", STATE_LABEL.get(s.get("state", ""), s.get("state", "—")))]
    if not file:
        conn += [("Uptime", fmt_duration(s["uptime_s"]) if s.get("uptime_s") else "—"),
                 ("Tersambung ulang", (f"{s.get('reconnects', 0)}× "
                                       f"({s.get('reconnects_10m', 0)}× dalam 10 menit)")),
                 ("Total terputus", fmt_duration(s.get("downtime_s", 0.0)))]
    backend = "NVIDIA DeepStream (GPU)" if s.get("backend") == "deepstream" else \
        "Python (Ultralytics)"
    analysis = [("Backend", backend + (" — fallback" if s.get("backend_note") else "")),
                ("Resolusi analisis", _res(s.get("resolution"))),
                ("FPS analisis", f"{s.get('proc_fps', 0.0):.1f}"
                                 + (f" (target {target:.0f})" if target and not file else "")),
                ("Frame", (f"diterima {s.get('total_frames', 0)} · "
                           f"dianalisis {s.get('decoded_frames', 0)}")),
                ("Dilewati sengaja", f"{s.get('skip_pct', 0.0):.0f}% (di atas FPS target)"),
                ("Hilang (beban)", f"{s.get('lost_pct', 0.0):.1f}%")]
    if not file:
        analysis.append(("Latensi", (f"{s.get('latency_ms', 0.0):.0f} ms "
                                     f"(p95 {s.get('latency_p95_ms', 0.0):.0f} ms)")))
    module = [("Modul", s.get("module", "—")), ("Objek terlacak", str(s.get("tracks", 0)))]
    module += list((s.get("metrics") or {}).items())
    tech = [("Deteksi", f"{s.get('detect_ms', 0.0):.1f} ms/frame"),
            ("Modul (tracker + analitik)", f"{s.get('module_ms', 0.0):.1f} ms/frame"),
            ("Gambar tile", f"{s.get('render_ms', 0.0):.1f} ms")]
    if s.get("backend_note"):
        tech.append(("Alasan fallback", s["backend_note"]))
    return [("Kamera", camera), ("Koneksi", conn), ("Analisis", analysis),
            ("Modul", module), ("Teknis", tech)]


class StreamInfoDialog(QDialog):
    """Panel rincian satu kamera; diperbarui tiap kali statistik baru datang (±1 detik)."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Info stream — {title}")
        self.setMinimumWidth(420)
        self._lay = QVBoxLayout(self)
        self._boxes: list[QWidget] = []
        self._empty = QLabel("Kamera belum berjalan.")
        self._empty.setStyleSheet(f"color:{theme.MUTED};")
        self._lay.addWidget(self._empty)

    def update_stats(self, s: dict | None) -> None:
        for w in self._boxes:
            w.deleteLater()
        self._boxes = []
        self._empty.setVisible(not s)
        if not s:
            return
        for title, rows in detail_sections(s):
            box = QGroupBox(title)
            form = QFormLayout(box)
            for k, v in rows:
                val = QLabel(str(v))
                val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                val.setWordWrap(True)
                form.addRow(k, val)
            self._lay.addWidget(box)
            self._boxes.append(box)


# ---------------------------------------------------------------- tile
class VideoTile(QWidget):
    toggle_requested = pyqtSignal(str)             # cam_id: minta start/stop

    def __init__(self, cam_id: str, title: str):
        super().__init__()
        self.cam_id = cam_id
        self.title = title
        self._stats: dict | None = None
        self._dialog: StreamInfoDialog | None = None
        self.header = QLabel(title)
        self.btn_info = QToolButton()
        self.btn_info.setAutoRaise(True)
        self.btn_info.setText("ⓘ")
        self.btn_info.setToolTip("Info stream")
        self.btn_info.clicked.connect(self.show_info)
        self.btn_toggle = QToolButton()
        self.btn_toggle.setAutoRaise(True)
        self.btn_toggle.clicked.connect(lambda: self.toggle_requested.emit(self.cam_id))
        self.view = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.view.setMinimumHeight(180)
        self.view.setStyleSheet("background:#111; color:#888;")
        self.footer = QLabel("—")
        self.footer.setWordWrap(True)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color:{theme.WARN};")
        self.warn.hide()

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.header, 1)
        top.addWidget(self.btn_info)
        top.addWidget(self.btn_toggle)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addLayout(top)
        for w in (self.view, self.footer, self.warn):
            lay.addWidget(w)
        lay.setStretch(1, 1)
        self.set_running(False)
        self.set_idle()

    def _paint_header(self, state: str) -> None:
        color = STATE_COLOR.get(state, "#757575")
        self.header.setStyleSheet(f"border-left:6px solid {color}; padding-left:6px;")
        self.header.setToolTip(STATE_LABEL.get(state, state))

    def set_running(self, running: bool) -> None:
        self.btn_toggle.setText("■" if running else "▶")
        self.btn_toggle.setToolTip("Hentikan kamera" if running else "Jalankan kamera")

    def set_idle(self) -> None:
        self._paint_header("stopped")
        self.view.setPixmap(QPixmap())
        self.view.setText("berhenti")
        self.footer.setStyleSheet("")
        self.footer.setText("—")
        self.footer.setToolTip("")
        self.warn.hide()
        self._stats = None
        if self._dialog is not None:
            self._dialog.update_stats(None)

    def set_frame(self, img: QImage) -> None:
        pix = QPixmap.fromImage(img).scaled(self.view.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
        self.view.setPixmap(pix)

    def set_stats(self, s: dict) -> None:
        self._stats = s
        self._paint_header(s["state"])
        self.footer.setStyleSheet("")
        self.footer.setText("\n".join(summary_lines(s)))
        self.footer.setToolTip("Klik ⓘ untuk rincian stream")
        warns = warnings(s)
        self.warn.setText("\n".join(f"⚠ {w}" for w in warns))
        self.warn.setVisible(bool(warns))
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.update_stats(s)

    def set_error(self, msg: str) -> None:
        self._paint_header("reconnecting")
        self.footer.setStyleSheet(f"color:{theme.BAD};")
        self.footer.setText(msg)
        self.footer.setToolTip(msg)

    def show_info(self) -> None:
        if self._dialog is None:
            self._dialog = StreamInfoDialog(self.title, self.window())
        self._dialog.update_stats(self._stats)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
