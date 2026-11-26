"""Halaman Analitik: dashboard yang dibangun dari `manifest.dashboard` modul (Fase 7 §3.4).

Host tidak tahu arti metrik — judul, label, satuan, dan cara agregasi diambil dari manifest.
Data dibaca di thread pool (koneksi SQLite read-only) agar GUI tidak membeku.

Tata letak: baris kartu KPI (metrik KPI modul, rata-rata event_stats, jam tersibuk dari grafik
per jam, uptime kamera), lalu grid 2 kolom berisi grafik (time series area, sebaran, per jam,
heatmap di atas citra kamera, tabel event).
"""
from __future__ import annotations

import time
from datetime import datetime

import cv2
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.annotate import fmt_value, render_heat
from aio_cctv.core.colors import PALETTE
from aio_cctv.gui import theme
from aio_cctv.gui.icons import icon
from aio_cctv.gui.utils import to_qimage
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.storage import queries as q
from aio_cctv.storage.dashboard_data import (
    AGG_LABELS,
    LiveTail,
    default_widgets,
    fmt_seconds,
    load_dashboard,
    with_gaps,
)
from aio_cctv.zones.models import to_pixels

RANGES = [("Hari ini", 1), ("7 hari", 7), ("30 hari", 30)]
CHART_H = 210                # tinggi MINIMUM area grafik; di layar tinggi grafik memanjang
CARD_OVERHEAD = 58           # judul + margin kartu grafik (px)
REFRESH_MS = 15_000          # muat ulang penuh; KPI & ujung grafik bergerak tiap detik
CHART_ORDER = {"timeseries": 0, "event_stats": 1, "hourly": 2, "heatmap": 3, "events": 4}
AREA_COLOR = (229, 72, 77)   # merah (grafik area real-time)
DIST_COLOR = "#4CAF50"       # hijau (sebaran)
HOUR_COLOR = "#1E88E5"       # biru (per jam)

QSS = f"""
QFrame#dashCard {{ background:{theme.SURFACE}; border:1px solid {theme.BORDER}; border-radius:8px; }}
QFrame#dashCard QLabel {{ border:none; background:transparent; }}
QLabel#dashTitle {{ font-size:15px; }}
QLabel#kpiBig {{ font-size:28px; font-weight:bold; }}
QLabel#kpiSub {{ color:{theme.MUTED}; }}
QLabel#bigTitle {{ font-size:22px; font-weight:bold; }}
"""


def _card(title: str, tip: str = "") -> tuple[QFrame, QVBoxLayout]:
    """Kartu bersudut: judul + ikon ⓘ (penjelasan) + isi."""
    f = QFrame(objectName="dashCard")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(6)
    head = QHBoxLayout()
    t = QLabel(title, objectName="dashTitle")
    t.setWordWrap(True)
    head.addWidget(t, 1)
    if tip:
        i = QLabel()
        i.setPixmap(icon("alert_circle", theme.MUTED).pixmap(16, 16))
        i.setToolTip(tip)
        head.addWidget(i, 0, Qt.AlignmentFlag.AlignTop)
    lay.addLayout(head)
    return f, lay


def _plot(time_axis: bool = True) -> pg.PlotWidget:
    p = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()} if time_axis else None)
    p.setBackground(None)
    p.showGrid(x=True, y=True, alpha=0.15)
    for ax in ("left", "bottom"):
        p.getAxis(ax).setTextPen(theme.MUTED)
        p.getAxis(ax).setPen(theme.BORDER_STRONG)
    p.setFixedHeight(CHART_H)                   # 2 baris grafik muat satu layar (seperti desain)
    p.setMenuEnabled(False)
    p.getViewBox().setLimits(yMin=0)            # jumlah/metrik tidak pernah negatif
    return p


def _fmt_pct(x: float) -> str:
    return f"{x:.1f}%"


class DashboardPage(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._loading = False
        self._pending = False                      # pilihan berubah saat data masih dimuat
        self._kpi_labels: dict[tuple[str, str], tuple] = {}   # (metric, region) -> label live
        self._live_curves: dict[str, dict] = {}    # metric -> kurva grafik waktu + menit berjalan
        self._tails: dict[tuple[str, str], LiveTail] = {}       # (kamera, metric) -> tetap antar-reload
        self._bg: dict[str, np.ndarray] = {}       # cam_id -> citra kamera terakhir (latar heatmap)
        self._range: tuple[float, float] = (0.0, 0.0)
        self._setup_ui()
        manager.cameras_changed.connect(self._reload_cameras)
        manager.modules_changed.connect(self.refresh)
        manager.stats_updated.connect(self._on_stats)
        self._reload_cameras()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    # ------------------------------------------------------------------ UI
    def _setup_ui(self) -> None:
        self.setStyleSheet(QSS)
        self.cbo_cam = QComboBox()
        self.cbo_cam.setMinimumWidth(180)
        self.cbo_cam.currentIndexChanged.connect(self.refresh)
        self.cbo_range = QComboBox()
        for label, days in RANGES:
            self.cbo_range.addItem(label, days)
        self.cbo_range.currentIndexChanged.connect(self.refresh)
        self.btn_refresh = QPushButton(icon("refresh", theme.TEXT), " Muat ulang")
        self.btn_refresh.setIconSize(QSize(15, 15))
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_pdf = QPushButton(icon("pdf", theme.TEXT), " Ekspor PDF")
        self.btn_pdf.setIconSize(QSize(15, 15))
        self.btn_pdf.setToolTip("Simpan laporan kamera ini (rentang terpilih) sebagai PDF")
        self.btn_pdf.clicked.connect(self.export_pdf)
        title = QLabel("Analitik Kamera", objectName="bigTitle")
        self.lbl_info = QLabel()
        self.lbl_info.setStyleSheet(f"color:{theme.MUTED};")

        top = QHBoxLayout()
        top.addWidget(title)
        top.addSpacing(8)
        top.addWidget(self.cbo_cam)
        top.addStretch()
        top.addWidget(QLabel("Rentang:"))
        top.addWidget(self.cbo_range)
        top.addWidget(self.btn_refresh)
        sub = QHBoxLayout()
        sub.addWidget(self.lbl_info, 1)
        sub.addWidget(self.btn_pdf)

        self.kpi_row = QHBoxLayout()
        self.kpi_row.setSpacing(12)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.host = QWidget()
        hl = QVBoxLayout(self.host)
        hl.setContentsMargins(0, 0, 6, 0)
        hl.setSpacing(12)
        hl.addLayout(self.kpi_row)
        hl.addLayout(self.grid)
        hl.addStretch()
        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.host)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(sub)
        lay.addSpacing(6)
        lay.addWidget(scroll, 1)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()
        self.timer.start(REFRESH_MS)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.timer.stop()

    def _reload_cameras(self) -> None:
        current = self.cbo_cam.currentData()
        self.cbo_cam.blockSignals(True)
        self.cbo_cam.clear()
        for cam in sorted(self.manager.cameras.values(), key=lambda c: c.name.lower()):
            self.cbo_cam.addItem(f"{cam.name} ({cam.location})" if cam.location else cam.name,
                                 cam.id)
        self.cbo_cam.setCurrentIndex(max(0, self.cbo_cam.findData(current)))
        self.cbo_cam.blockSignals(False)
        if self.isVisible():
            self.refresh()

    def _context(self):
        cam = self.manager.cameras.get(self.cbo_cam.currentData())
        if cam is None:
            return None
        profile = self.manager.profile(cam)
        info = self.manager.modules.get(profile.module.module_id)
        manifest = info.manifest if info is not None else None
        return cam, manifest, profile

    # ------------------------------------------------------------------ data
    def refresh(self) -> None:
        if not self.isVisible():
            return
        if self._loading:
            self._pending = True
            return
        ctx = self._context()
        if ctx is None:
            self._clear()
            self.lbl_info.setText("Belum ada kamera.")
            return
        cam, manifest, _ = ctx
        if manifest is None:
            self._clear()
            self.lbl_info.setText(f"Modul “{self.manager.module_id(cam)}” kamera ini tidak "
                                  "terpasang/valid — dashboard butuh manifest untuk label metrik.")
            return
        widgets = ctx[2].dashboard or manifest.dashboard or default_widgets(manifest)
        start, end = q.day_range(self.cbo_range.currentData() or 1)
        self._range = (start, end)
        self._loading = True
        self.lbl_info.setText("Memuat…")
        live = self.manager._last_stats.get(cam.id, {}).get("live_metrics") \
            if self.manager.is_running(cam.id) else None
        run_async(load_dashboard, self.manager.db_path, cam.id, manifest, widgets, start, end, live,
                  on_done=lambda data: self._render(data, ctx, start, end),
                  on_failed=self._failed)

    def _finish_loading(self) -> bool:
        """True bila hasil ini sudah basi dan muat ulang sudah dijadwalkan."""
        self._loading = False
        if self._pending:
            self._pending = False
            self.refresh()
            return True
        return False

    def _failed(self, msg: str) -> None:
        if not self._finish_loading():
            self.lbl_info.setText(f"Gagal memuat data: {msg}")

    def _on_stats(self, cam_id: str, s: dict) -> None:
        """KPI "sekarang" ikut bergerak tiap detik; grafik tetap dimuat ulang berkala."""
        if cam_id != self.cbo_cam.currentData() or not self.isVisible():
            return
        now = time.time()
        for key, region, value in s.get("live_metrics", []):
            if (lbl := self._kpi_labels.get((key, region or ""))) is not None:
                big, unit = lbl
                big.setText(f"{fmt_value(value)}{unit}")
            if key in self._live_curves:
                self._live_curves[key]["tail"].add(region or "", value, now)
        for key in self._live_curves:
            self._redraw_tail(key)

    def _redraw_tail(self, key: str) -> None:
        live = self._live_curves[key]
        for reg, (curve, gx, gy, last_x) in live["curves"].items():
            tx, ty = live["tail"].points(reg, after=last_x)
            curve.setData(gx + tx, gy + ty, connect="finite")

    def _clear(self) -> None:
        self._kpi_labels = {}
        self._live_curves = {}
        self._chart_widgets: list[QWidget] = []
        self._heat_label: QLabel | None = None
        self._heat_pix: QPixmap | None = None
        for lay in (self.kpi_row, self.grid):
            while lay.count():
                item = lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

    def _render(self, data: list[dict], ctx, start: float, end: float) -> None:
        if self._finish_loading():
            return
        cam, manifest, profile = ctx
        names = {z.id: z.name for z in profile.zones} | {ln.id: ln.name for ln in profile.lines} \
            if profile else {}
        self._clear()
        labels = {e.type: e.label or e.type for e in manifest.events}

        # --- baris KPI
        kpis: list[QWidget] = []
        for d in data:
            kind = d["spec"].get("type")
            if kind == "kpi":
                kpis.append(self._kpi_metric(d, manifest, names))
            elif kind == "event_stats":
                kpis.append(self._kpi_event_stats(d, manifest, names))
        if (hourly := next((d for d in data if d["spec"].get("type") == "hourly"), None)):
            kpis.append(self._kpi_peak(hourly, labels))
        if (health := next((d for d in data if d["spec"].get("type") == "health"), None)):
            kpis.append(self._kpi_uptime(health))
        for w in kpis:
            if w is not None:
                self.kpi_row.addWidget(w, 1)

        # --- grid grafik 2 kolom
        charts = [d for d in data if d["spec"].get("type") in CHART_ORDER]
        charts.sort(key=lambda d: CHART_ORDER[d["spec"]["type"]])
        i = 0
        for d in charts:
            w = self._chart(d, manifest, profile, names, labels, cam.id)
            if w is not None:
                self.grid.addWidget(w, i // 2, i % 2)
                i += 1
        self._rows = (i + 1) // 2
        QTimer.singleShot(0, self._fit_charts)    # setelah tata letak KPI selesai

        self.lbl_info.setText(f"Modul: {manifest.name} • "
                              f"{datetime.fromtimestamp(start):%d-%m-%Y %H:%M} - "
                              f"{datetime.fromtimestamp(end):%H:%M}"
                              if self.cbo_range.currentData() == 1 else
                              f"Modul: {manifest.name} • "
                              f"{datetime.fromtimestamp(start):%d-%m-%Y} - "
                              f"{datetime.fromtimestamp(end):%d-%m-%Y}")
        self.lbl_info.setToolTip(f"Diperbarui {datetime.now():%H:%M:%S}")

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_charts()

    def _fit_charts(self) -> None:
        """Tinggi grafik mengisi sisa layar di bawah kartu KPI (min CHART_H): di layar tinggi
        tidak ada ruang kosong, di layar pendek halaman tetap bisa digulir."""
        widgets = getattr(self, "_chart_widgets", [])
        rows = getattr(self, "_rows", 0)
        if not widgets or not rows:
            return
        kpi_h = max((self.kpi_row.itemAt(i).widget().sizeHint().height()
                     for i in range(self.kpi_row.count()) if self.kpi_row.itemAt(i).widget()),
                    default=0)
        spacing = self.grid.verticalSpacing() * (rows - 1) + 12
        free = self.scroll.viewport().height() - kpi_h - spacing - 8
        h = max(CHART_H, free // rows - CARD_OVERHEAD)
        for w in widgets:
            w.setFixedHeight(h)
        lbl = getattr(self, "_heat_label", None)
        if lbl is not None and getattr(self, "_heat_pix", None) is not None:
            lbl.setPixmap(self._heat_pix.scaledToHeight(
                h, Qt.TransformationMode.SmoothTransformation))

    # ---------------------------------------------------------------- KPI
    def _kpi_card(self, title: str, big: str, sub: str, tip: str) -> tuple[QFrame, QLabel]:
        card, lay = _card(title, tip)
        b = QLabel(big, objectName="kpiBig")
        s = QLabel(sub, objectName="kpiSub")
        s.setWordWrap(True)
        lay.addWidget(b)
        lay.addWidget(s)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        return card, b

    def _kpi_metric(self, d: dict, manifest, names: dict) -> QWidget | None:
        w = d["spec"]
        spec = manifest.metric(w["metric"]) if w.get("metric") else None
        if spec is None:
            return None
        unit = f" {spec.unit}" if spec.unit else ""
        title = w.get("title") or spec.label
        vals, now, latest, mx = (d.get(k, {}) for k in ("values", "now", "latest", "max"))
        regions = sorted(set(vals) | set(now) | set(latest))
        reg = "" if "" in regions else (regions[0] if regions else None)
        rng = self.cbo_range.currentText().lower()
        if reg is None:
            card, big = self._kpi_card(title, "—", f"belum ada data ({rng})",
                                       "Metrik KPI dari modul analitik kamera ini.")
            return card
        if reg in now:
            value = now[reg]
        elif reg in latest:
            value = latest[reg][1]
        else:
            value = vals.get(reg, 0)
        parts = []
        if reg in mx:
            parts.append(f"maks {fmt_value(mx[reg])}{unit}")
        if reg in vals:
            parts.append(f"{AGG_LABELS.get(spec.agg, spec.agg)} {fmt_value(vals[reg])}{unit}")
        where = f"{names.get(reg, reg)} · " if reg else ""
        tip = (f"Nilai sekarang (bergerak tiap detik saat kamera berjalan). Di bawahnya ringkasan "
               f"{rng}: maks = tertinggi, {AGG_LABELS.get(spec.agg, spec.agg)} = cara modul "
               "merangkum metrik ini.")
        card, big = self._kpi_card(title, f"{fmt_value(value)}{unit}", where + " / ".join(parts),
                                   tip)
        self._kpi_labels[(spec.key, reg)] = (big, unit)
        return card

    def _kpi_event_stats(self, d: dict, manifest, names: dict) -> QWidget:
        w = d["spec"]
        ev = {e.type: e for e in manifest.events}.get(w.get("event"))
        title = (w.get("title") or (ev.label if ev else w.get("event", ""))) + " (Rata-rata)"
        fv = fmt_seconds if d.get("seconds") else fmt_value
        regs = d.get("regions", {})
        if not regs:
            card, _ = self._kpi_card(title, "—", "belum ada data pada rentang ini",
                                     "Rata-rata nilai event dari modul.")
            return card
        reg = "" if "" in regs else max(regs, key=lambda r: regs[r]["n"])
        st = regs[reg]
        where = f"{names.get(reg, reg)} · " if reg else ""
        card, _ = self._kpi_card(title, fv(st["avg"]),
                                 f"{where}median {fv(st['median'])} / terlama {fv(st['max'])}",
                                 f"Dari {st['n']} kejadian pada rentang terpilih.")
        return card

    def _kpi_peak(self, d: dict, labels: dict) -> QWidget:
        bars = d.get("bars") or [0] * 24
        ev = labels.get(d["spec"].get("event"), d["spec"].get("event", ""))
        if not any(bars):
            card, _ = self._kpi_card("Waktu Tersibuk (Peak)", "—", "belum ada data",
                                     "Jam dengan kejadian terbanyak.")
            return card
        h = int(np.argmax(bars))
        card, _ = self._kpi_card("Waktu Tersibuk (Peak)", f"{h:02d}:00 - {(h + 1) % 24:02d}:00",
                                 f"{bars[h]} {ev.lower()}",
                                 f"Jam dengan '{ev}' terbanyak pada rentang terpilih.")
        return card

    def _kpi_uptime(self, d: dict) -> QWidget:
        on, total = d.get("uptime", (0, 0))
        if not total:
            card, _ = self._kpi_card("Uptime Kamera", "—", "belum ada data kesehatan",
                                     "Persentase menit kamera berjalan normal.")
            return card
        card, _ = self._kpi_card("Uptime Kamera", _fmt_pct(100 * on / total),
                                 f"dari {total} mnt tercatat",
                                 "Persentase menit tercatat saat kamera streaming normal.")
        return card

    # -------------------------------------------------------------- grafik
    def _chart(self, d: dict, manifest, profile, names: dict, labels: dict,
               cam_id: str) -> QWidget | None:
        w = d["spec"]
        kind = w.get("type")
        spec = manifest.metric(w["metric"]) if w.get("metric") else None
        ev = {e.type: e for e in manifest.events}.get(w.get("event"))
        title = w.get("title") or (spec.label if spec else ev.label if ev else kind) or ""

        if kind == "timeseries" and spec:
            live = self.cbo_range.currentData() == 1 and self.manager.is_running(cam_id)
            card, lay = _card(f"{title}{' Real-time' if live else ''}"
                              f"{' (' + spec.unit + ')' if spec.unit else ''}",
                              "Nilai metrik per menit; ujung grafik bergerak tiap detik saat "
                              "kamera berjalan.")
            p = _plot()
            p.addLegend(offset=(5, 5))
            bucket = d.get("bucket_s", 60)
            curves = {}
            series = sorted(d.get("series", {}).items())
            for i, (reg, (xs, ys)) in enumerate(series):
                gx, gy = with_gaps(xs, ys, bucket)              # celah data = garis terputus
                r, g, b = AREA_COLOR if i == 0 else QColor(PALETTE[i % len(PALETTE)]).getRgb()[:3]
                curve = p.plot(gx, gy, pen=pg.mkPen((r, g, b), width=2), connect="finite",
                               name=names.get(reg, reg) or "kamera",
                               fillLevel=0 if len(series) == 1 else None,
                               brush=(r, g, b, 70) if len(series) == 1 else None)
                curves[reg] = (curve, gx, gy, xs[-1] if xs else float("-inf"))
            if bucket == 60:                                    # ujung live hanya untuk per menit
                key = (cam_id, spec.key)
                tail = self._tails.setdefault(key, LiveTail(spec.agg))
                self._live_curves[spec.key] = {"curves": curves, "tail": tail}
                self._redraw_tail(spec.key)
            lay.addWidget(p)
            self._chart_widgets.append(p)
            return card

        if kind == "event_stats":
            if not any(d.get("counts", [])):
                return None
            card, lay = _card(f"Sebaran {title}", "Jumlah kejadian per rentang nilai.")
            p = _plot(time_axis=False)
            p.addItem(pg.BarGraphItem(x=list(range(len(d["counts"]))), height=d["counts"],
                                      width=0.8, brush=DIST_COLOR, pen=None))
            p.getAxis("bottom").setTicks([list(enumerate(d["bins"]))])
            lay.addWidget(p)
            self._chart_widgets.append(p)
            return card

        if kind == "hourly":
            card, lay = _card(f"{title} (jam 00–23)",
                              f"Jumlah '{ev.label if ev else w.get('event')}' per jam dalam sehari.")
            p = _plot(time_axis=False)
            bars = d.get("bars", [0] * 24)
            p.addItem(pg.BarGraphItem(x=list(range(24)), height=bars, width=0.7,
                                      brush=HOUR_COLOR, pen=None))
            p.getAxis("bottom").setTicks([[(h, f"{h:02d}") for h in range(0, 24, 2)]])
            lay.addWidget(p)
            self._chart_widgets.append(p)
            return card

        if kind == "heatmap":
            card, lay = _card("Heatmap Aktivitas" if title in ("", "heatmap") else title,
                              "Area yang paling sering dilewati/ditempati orang pada rentang "
                              "terpilih (merah = paling sering).")
            lbl = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lbl.setFixedHeight(CHART_H)
            heat = d.get("heat")
            if heat is None:
                lbl.setText("Belum ada data heatmap pada rentang ini.")
                lbl.setStyleSheet(f"color:{theme.MUTED};")
            else:
                bg = self._background(cam_id)
                if bg is not None:
                    fh, fw = bg.shape[:2]
                else:
                    fw, fh = (profile.frame_size if profile and profile.frame_size else (640, 360))
                w_px = 640
                size = (w_px, max(1, round(w_px * fh / fw)))
                bg_s = cv2.resize(bg, size) if bg is not None else None
                img = render_heat(heat, size, bg_s, alpha=0.5)
                if profile:                          # garis zona sebagai acuan
                    for z in profile.zones:
                        cv2.polylines(img, [to_pixels(z.polygon, *size)], True, (255, 255, 255), 1)
                self._heat_pix = QPixmap.fromImage(to_qimage(np.ascontiguousarray(img)))
                lbl.setPixmap(self._heat_pix.scaledToHeight(
                    CHART_H, Qt.TransformationMode.SmoothTransformation))
                if bg is None:
                    lbl.setToolTip("Jalankan kamera sekali agar citranya dipakai sebagai latar.")
                self._heat_label = lbl
            lay.addWidget(lbl)
            self._chart_widgets.append(lbl)
            return card

        if kind == "events":
            rows = d.get("rows", [])
            card, lay = _card(f"{title or 'Event'} — {len(rows)} terbaru")
            t = QTableWidget(len(rows), 5)
            t.setObjectName("plain")
            t.setHorizontalHeaderLabels(["Waktu", "Event", "Zona", "Track", "Data"])
            t.verticalHeader().hide()
            for r, e in enumerate(rows):
                vals = [datetime.fromtimestamp(e["ts"]).strftime("%d-%m %H:%M:%S"),
                        labels.get(e["type"], e["type"]),
                        names.get(e["region_id"], e["region_id"] or ""),
                        "" if e["track_id"] is None else e["track_id"],
                        ", ".join(f"{k}={fmt_value(v) if isinstance(v, float) else v}"
                                  for k, v in e["data"].items())]
                for c, v in enumerate(vals):
                    t.setItem(r, c, QTableWidgetItem(str(v)))
            t.resizeColumnsToContents()
            t.setFixedHeight(CHART_H)
            lay.addWidget(t)
            self._chart_widgets.append(t)
            return card
        return None

    def _background(self, cam_id: str) -> np.ndarray | None:
        """Citra kamera terakhir (dari FrameHub saat berjalan; disimpan untuk sesi ini).
        Mode anonim: pakai versi yang sudah diblur."""
        frame = self.manager.frames.get(cam_id)
        if frame is not None:
            self._bg[cam_id] = (frame.annotated if frame.blurred else frame.raw).copy()
        return self._bg.get(cam_id)

    # ----------------------------------------------------------------- PDF
    def export_pdf(self) -> None:
        ctx = self._context()
        if ctx is None:
            return
        cam, manifest, profile = ctx
        default = f"analitik-{cam.id}-{datetime.now():%Y%m%d}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Simpan analitik PDF", default, "PDF (*.pdf)")
        if not path:
            return
        from aio_cctv import __version__
        from aio_cctv.reports.pdf_report import CameraInfo, collect, html_to_pdf, render_html
        start, end = self._range
        self.manager.storage.flush(timeout=3)          # data menit terakhir ikut masuk
        info = CameraInfo(cam.id, cam.name, manifest, profile.region_names)
        self.btn_pdf.setEnabled(False)
        self.lbl_info.setText("Membuat PDF…")

        def done(reports) -> None:
            html, images = render_html(reports, start, end, __version__)
            html_to_pdf(html, path, images)
            self.btn_pdf.setEnabled(True)
            self.lbl_info.setText(f"PDF tersimpan: {path}")

        def failed(msg: str) -> None:
            self.btn_pdf.setEnabled(True)
            self.lbl_info.setText(f"Gagal membuat PDF: {msg}")

        run_async(collect, self.manager.db_path, [info], start, end, on_done=done,
                  on_failed=failed)
