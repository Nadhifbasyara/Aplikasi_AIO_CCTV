"""Tile kamera di Live View + panel "Info stream".

Video dalam kartu bersudut; di atasnya overlay nama kamera, peringatan (HANYA bila ada masalah),
dan kartu KPI metrik modul. Di bawah video: resolusi | FPS kamera, tombol perbesar, ⓘ, start/stop.
Rincian lengkap ada di panel Info stream (tombol ⓘ). Waktu per tahap (deteksi/modul) tetap tercatat untuk benchmark, tetapi
hanya tampil di bagian "Teknis" panel.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGraphicsDropShadowEffect,
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
    if s.get("ds_error"):
        out.append(f"DeepStream: {s['ds_error']} — mencoba lagi otomatis")
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
class VideoView(QLabel):
    """Area video + overlay: nama kamera (kiri atas), peringatan (kanan atas), dan kartu KPI
    metrik modul (kiri bawah). Overlay ikut berpindah saat ukuran tile berubah."""

    KPI_STYLES = (
        # kartu pertama menonjol (hijau bercahaya), sisanya gelap — seperti desain Live
        ("background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1FA971, stop:1 #2ECC8F);"
         "border:2px solid #7DF5C4; color:white;"),
        "background:#262A2E; border:2px solid #5A6068; color:white;",
    )

    def __init__(self):
        super().__init__(alignment=Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("videoView")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumHeight(180)
        self.setStyleSheet("QLabel#videoView { background:#111316; color:#888;"
                           " border:1px solid #3A3F45; border-radius:10px; }")
        self.name_chip = QLabel(self)
        self.name_chip.setStyleSheet("background:rgba(0,0,0,150); color:white; border-radius:5px;"
                                     " padding:3px 9px;")
        self.warn_chip = QLabel(self)
        self.warn_chip.setWordWrap(True)
        self.warn_chip.setStyleSheet("background:rgba(255,167,38,225); color:#1B1B1B;"
                                     " border-radius:5px; padding:3px 9px; font-weight:bold;")
        self.warn_chip.hide()
        self.kpi_box = QWidget(self)
        self.kpi_lay = QVBoxLayout(self.kpi_box)
        self.kpi_lay.setContentsMargins(0, 0, 0, 0)
        self.kpi_lay.setSpacing(10)
        self.kpis: list[QLabel] = []

    def set_name(self, html: str) -> None:
        self.name_chip.setText(html)
        self.name_chip.adjustSize()
        self._place()

    def set_warning(self, text: str, color: str = theme.WARN) -> None:
        self.warn_chip.setText(text)
        self.warn_chip.setStyleSheet(f"background:{color}; color:#1B1B1B; border-radius:5px;"
                                     f" padding:3px 9px; font-weight:bold;")
        self.warn_chip.setVisible(bool(text))
        self._place()

    def set_kpis(self, items: list[tuple[str, str]]) -> None:
        while len(self.kpis) < len(items):
            card = QLabel()
            card.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if not self.kpis:                       # cahaya hijau hanya kartu pertama
                glow = QGraphicsDropShadowEffect(card)
                glow.setBlurRadius(28)
                glow.setOffset(0, 0)
                glow.setColor(QColor(46, 204, 143, 170))
                card.setGraphicsEffect(glow)
            self.kpi_lay.addWidget(card)
            self.kpis.append(card)
        fs = max(10, min(17, self.width() // 55))   # tile kecil (grid 3x3) → huruf kecil
        for i, card in enumerate(self.kpis):
            if i < len(items):
                label, value = items[i]
                card.setText(f"{label.upper()}: {value}")
                card.setStyleSheet(self.KPI_STYLES[min(i, 1)] +
                                   f"border-radius:9px; padding:{fs // 2}px {fs + 6}px;"
                                   f" font-size:{fs}px; font-weight:bold;")
                card.show()
            else:
                card.hide()
        self.kpi_box.adjustSize()
        self._place()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place()

    def _place(self) -> None:
        m = 12
        self.name_chip.move(m, m)
        if self.warn_chip.isVisible():
            self.warn_chip.setMaximumWidth(max(120, self.width() // 2))
            self.warn_chip.adjustSize()
            self.warn_chip.move(self.width() - self.warn_chip.width() - m, m)
        self.kpi_box.adjustSize()
        self.kpi_box.move(m, self.height() - self.kpi_box.height() - m)


def kpi_items(s: dict, limit: int = 2) -> list[tuple[str, str]]:
    """Metrik tingkat kamera dari modul → kartu KPI. Nilai tanpa satuan, mis. ("Orang", "3")."""
    out = []
    for label, value in list((s.get("metrics") or {}).items())[:limit]:
        out.append((label, str(value).split(" ")[0]))
    return out


class VideoTile(QWidget):
    toggle_requested = pyqtSignal(str)             # cam_id: minta start/stop
    focus_requested = pyqtSignal(str)              # cam_id: tampilkan kamera ini saja

    def __init__(self, cam_id: str, title: str):
        super().__init__()
        self.cam_id = cam_id
        self.title = title
        self._state = "stopped"
        self._stats: dict | None = None
        self._dialog: StreamInfoDialog | None = None
        self.view = VideoView()

        def tool(text: str, tip: str) -> QToolButton:
            b = QToolButton()
            b.setAutoRaise(True)
            b.setText(text)
            b.setToolTip(tip)
            return b

        self.btn_focus = tool("⛶", "Perbesar kamera ini")
        self.btn_focus.clicked.connect(lambda: self.focus_requested.emit(self.cam_id))
        self.btn_info = tool("ⓘ", "Info stream")
        self.btn_info.clicked.connect(self.show_info)
        self.btn_toggle = tool("▶", "Jalankan kamera")
        self.btn_toggle.clicked.connect(lambda: self.toggle_requested.emit(self.cam_id))
        self.info = QLabel("—")
        self.info.setStyleSheet(f"color:{theme.MUTED};")

        # tombol ⛶ rata dengan garis kiri kartu video (tanpa jarak/padding tambahan)
        self.btn_focus.setStyleSheet("QToolButton { padding:0px; margin:0px; }")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 2, 0)
        row.setSpacing(8)
        row.addWidget(self.btn_focus)
        row.addWidget(self.info, 1)
        row.addWidget(self.btn_info)
        row.addWidget(self.btn_toggle)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(4)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        self.set_title(title)
        self.set_running(False)
        self.set_idle()

    # ---------------------------------------------------------------- tampilan
    def set_title(self, title: str) -> None:
        self.title = title
        self._paint_name()

    def _paint_name(self) -> None:
        color = STATE_COLOR.get(self._state, "#757575")
        self.view.set_name(f"<span style='color:{color}'>●</span>&nbsp;{self.title}")
        self.view.name_chip.setToolTip(STATE_LABEL.get(self._state, self._state))

    def set_focused(self, focused: bool) -> None:
        self.btn_focus.setText("⊞" if focused else "⛶")
        self.btn_focus.setToolTip("Kembali ke grid" if focused else "Perbesar kamera ini")

    def set_running(self, running: bool) -> None:
        self.btn_toggle.setText("■" if running else "▶")
        self.btn_toggle.setToolTip("Hentikan kamera" if running else "Jalankan kamera")

    def set_idle(self) -> None:
        self._state = "stopped"
        self._paint_name()
        self.view.setPixmap(QPixmap())
        self.view.setText("berhenti")
        self.view.set_kpis([])
        self.view.set_warning("")
        self.info.setStyleSheet(f"color:{theme.MUTED};")
        self.info.setText("—")
        self.info.setToolTip("")
        self._stats = None
        if self._dialog is not None:
            self._dialog.update_stats(None)

    def set_frame(self, img: QImage) -> None:
        pix = QPixmap.fromImage(img).scaled(self.view.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
        self.view.setPixmap(pix)

    def set_stats(self, s: dict) -> None:
        self._stats = s
        self._state = s["state"]
        self._paint_name()
        w, h = s.get("source_resolution") or s.get("resolution") or (0, 0)
        self.info.setStyleSheet(f"color:{theme.MUTED};")
        self.info.setText(f"{w}x{h} | {s.get('stream_fps', 0.0):.0f} FPS" if w else "—")
        self.info.setToolTip("\n".join(summary_lines(s)) + "\n\nKlik ⓘ untuk rincian stream")
        self.view.set_kpis(kpi_items(s))
        self.view.set_warning(" · ".join(f"⚠ {w}" for w in warnings(s)))
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.update_stats(s)

    def set_error(self, msg: str) -> None:
        self._state = "reconnecting"
        self._paint_name()
        self.info.setStyleSheet(f"color:{theme.BAD};")
        self.info.setText(msg)
        self.info.setToolTip(msg)
        self.view.set_warning(f"⚠ {msg}", "rgba(239,83,80,230)")

    def show_info(self) -> None:
        if self._dialog is None:
            self._dialog = StreamInfoDialog(self.title, self.window())
        self._dialog.update_stats(self._stats)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
