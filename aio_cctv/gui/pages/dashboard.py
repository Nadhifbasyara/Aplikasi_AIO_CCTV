"""Halaman Analitik: dashboard yang dibangun dari `manifest.dashboard` modul (Fase 7 §3.4).

Host tidak tahu arti metrik — judul, label, satuan, dan cara agregasi diambil dari manifest.
Data dibaca di thread pool (koneksi SQLite read-only) agar GUI tidak membeku.
"""
from __future__ import annotations

import time
from datetime import datetime

import cv2
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.annotate import fmt_value, render_heat
from aio_cctv.core.colors import PALETTE
from aio_cctv.gui.utils import to_qimage
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.storage import queries as q
from aio_cctv.storage.dashboard_data import (
    AGG_LABELS,
    LiveTail,
    default_widgets,
    load_dashboard,
    with_gaps,
)
from aio_cctv.zones.models import to_pixels

RANGES = [("Hari ini", 1), ("7 hari", 7), ("30 hari", 30)]
REFRESH_MS = 15_000          # muat ulang penuh; KPI & ujung grafik bergerak tiap detik


def _plot(title: str, time_axis: bool = True) -> pg.PlotWidget:
    p = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()} if time_axis else None)
    p.setBackground(None)
    p.setTitle(title)
    p.showGrid(x=True, y=True, alpha=0.2)
    p.addLegend(offset=(5, 5))
    p.setMinimumHeight(220)
    return p


class DashboardPage(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._loading = False
        self._pending = False                      # pilihan berubah saat data masih dimuat
        self._kpi_labels: dict[tuple[str, str], tuple] = {}   # (metric, region) -> label live
        self._live_curves: dict[str, dict] = {}    # metric -> kurva grafik waktu + menit berjalan
        self._tails: dict[tuple[str, str], LiveTail] = {}       # (kamera, metric) -> tetap antar-reload
        self._setup_ui()
        manager.cameras_changed.connect(self._reload_cameras)
        manager.modules_changed.connect(self.refresh)
        manager.stats_updated.connect(self._on_stats)
        self._reload_cameras()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    def _setup_ui(self) -> None:
        self.cbo_cam = QComboBox()
        self.cbo_cam.currentIndexChanged.connect(self.refresh)
        self.cbo_range = QComboBox()
        for label, days in RANGES:
            self.cbo_range.addItem(label, days)
        self.cbo_range.currentIndexChanged.connect(self.refresh)
        self.btn_refresh = QPushButton("Muat ulang")
        self.btn_refresh.clicked.connect(self.refresh)
        self.lbl_info = QLabel()
        self.lbl_info.setStyleSheet("color:#888;")

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Kamera:"))
        bar.addWidget(self.cbo_cam, 1)
        bar.addWidget(QLabel("Rentang:"))
        bar.addWidget(self.cbo_range)
        bar.addWidget(self.btn_refresh)

        self.host = QWidget()
        self.grid = QGridLayout(self.host)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.host)

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.lbl_info)
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
            self.cbo_cam.addItem(cam.name, cam.id)
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
            self.lbl_info.setText(f"Modul “{self.manager.module_id(cam)}” kamera ini tidak terpasang/valid — "
                                  "dashboard butuh manifest untuk label metrik.")
            return
        widgets = ctx[2].dashboard or manifest.dashboard or default_widgets(manifest)
        start, end = q.day_range(self.cbo_range.currentData() or 1)
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
        """KPI "sekarang" ikut bergerak tiap detik; grafik tetap dimuat ulang tiap 30 detik."""
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
        while self.grid.count():
            self.grid.takeAt(0).widget().deleteLater()

    def _render(self, data: list[dict], ctx, start: float, end: float) -> None:
        if self._finish_loading():
            return
        cam, manifest, profile = ctx
        names = {z.id: z.name for z in profile.zones} | {ln.id: ln.name for ln in profile.lines} \
            if profile else {}
        self._clear()
        row = col = 0
        for d in data:
            w = self._widget(d, manifest, profile, names)
            if w is None:
                continue
            span = 1 if d["spec"]["type"] == "kpi" else 2
            if col + span > 2:
                row, col = row + 1, 0
            self.grid.addWidget(w, row, col, 1, span)
            col += span
        self.lbl_info.setText(f"{cam.name} · modul {manifest.name} · "
                              f"{datetime.fromtimestamp(start):%d-%m-%Y %H:%M} – "
                              f"{datetime.fromtimestamp(end):%d-%m-%Y %H:%M} · "
                              f"diperbarui {datetime.now():%H:%M:%S}")

    # --------------------------------------------------------------- widget
    def _widget(self, d: dict, manifest, profile, names: dict) -> QWidget | None:
        w = d["spec"]
        kind = w.get("type")
        spec = manifest.metric(w["metric"]) if w.get("metric") else None
        ev = {e.type: e for e in manifest.events}.get(w.get("event"))
        title = w.get("title") or (spec.label if spec else ev.label if ev else kind) or ""
        unit = f" {spec.unit}" if spec and spec.unit else ""

        if kind == "kpi" and spec:
            box = QGroupBox(title)
            lay = QVBoxLayout(box)
            vals, now, latest, mx = (d.get(k, {}) for k in ("values", "now", "latest", "max"))
            regions = sorted(set(vals) | set(now))
            if not regions:
                lay.addWidget(QLabel("—"))
            for reg in regions:
                # angka besar = keadaan SEKARANG (live) / terakhir tercatat; rata-rata di bawahnya
                if reg in now:
                    big, when = now[reg], "sekarang"
                elif reg in latest:
                    ts, big = latest[reg]
                    when = f"terakhir, pukul {datetime.fromtimestamp(ts):%H:%M}"
                else:
                    big, when = vals[reg], AGG_LABELS.get(spec.agg, spec.agg)
                lbl = QLabel(f"{fmt_value(big)}{unit}")
                lbl.setStyleSheet("font-size:26px; font-weight:bold;")
                lay.addWidget(lbl)
                self._kpi_labels[(spec.key, reg)] = (lbl, unit)
                where = f"{names.get(reg, reg)} · " if reg else ""
                summary = f"{AGG_LABELS.get(spec.agg, spec.agg)} {fmt_value(vals[reg])}{unit}" \
                    if reg in vals else "belum ada data tersimpan"
                if reg in mx:
                    summary += f" · maks {fmt_value(mx[reg])}{unit}"
                lay.addWidget(QLabel(f"<span style='color:#888'>{where}{when} · "
                                     f"{self.cbo_range.currentText().lower()}: {summary}</span>"))
            return box

        if kind == "timeseries" and spec:
            p = _plot(f"{title}{' (' + spec.unit + ')' if spec.unit else ''}")
            bucket = d.get("bucket_s", 60)
            curves = {}
            for i, (reg, (xs, ys)) in enumerate(sorted(d.get("series", {}).items())):
                gx, gy = with_gaps(xs, ys, bucket)              # celah data = garis terputus
                curve = p.plot(gx, gy, pen=pg.mkPen(PALETTE[i % len(PALETTE)], width=2),
                               connect="finite", name=names.get(reg, reg) or "kamera")
                curves[reg] = (curve, gx, gy, xs[-1] if xs else float("-inf"))
            if bucket == 60:                                    # ujung live hanya untuk per menit
                key = (self.cbo_cam.currentData(), spec.key)
                tail = self._tails.setdefault(key, LiveTail(spec.agg))
                self._live_curves[spec.key] = {"curves": curves, "tail": tail}
                self._redraw_tail(spec.key)
            return p

        if kind == "hourly":
            p = _plot(f"{title} (jam 00–23)", time_axis=False)
            bars = d.get("bars", [0] * 24)
            p.addItem(pg.BarGraphItem(x=list(range(24)), height=bars, width=0.8,
                                      brush=PALETTE[1]))
            p.getAxis("bottom").setTicks([[(h, f"{h:02d}") for h in range(0, 24, 2)]])
            return p

        if kind == "events":
            rows = d.get("rows", [])
            box = QGroupBox(f"{title or 'Event'} — {len(rows)} terbaru")
            t = QTableWidget(len(rows), 5)
            t.setHorizontalHeaderLabels(["Waktu", "Event", "Zona", "Track", "Data"])
            labels = {e.type: e.label or e.type for e in manifest.events}
            for r, e in enumerate(rows):
                vals = [datetime.fromtimestamp(e["ts"]).strftime("%d-%m %H:%M:%S"),
                        labels.get(e["type"], e["type"]), names.get(e["region_id"], e["region_id"] or ""),
                        "" if e["track_id"] is None else e["track_id"],
                        ", ".join(f"{k}={fmt_value(v) if isinstance(v, float) else v}"
                                  for k, v in e["data"].items())]
                for c, v in enumerate(vals):
                    t.setItem(r, c, QTableWidgetItem(str(v)))
            t.resizeColumnsToContents()
            t.setMinimumHeight(220)
            QVBoxLayout(box).addWidget(t)
            return box

        if kind == "heatmap":
            box = QGroupBox(title if title != "heatmap" else "Heatmap")
            lbl = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
            heat = d.get("heat")
            if heat is None:
                lbl.setText("Belum ada data heatmap pada rentang ini.")
            else:
                fw, fh = (profile.frame_size if profile and profile.frame_size else (640, 360))
                w_px = 560
                size = (w_px, max(1, round(w_px * fh / fw)))
                img = render_heat(heat, size)
                if profile:                          # garis zona sebagai acuan (tanpa citra kamera)
                    for z in profile.zones:
                        cv2.polylines(img, [to_pixels(z.polygon, *size)], True, (255, 255, 255), 1)
                lbl.setPixmap(QPixmap.fromImage(to_qimage(np.ascontiguousarray(img))))
            QVBoxLayout(box).addWidget(lbl)
            return box

        if kind == "health":
            return self._health_widget(d, manifest)
        return None

    def _health_widget(self, d: dict, manifest) -> QWidget:
        box = QGroupBox("Kinerja && kesehatan kamera")
        lay = QVBoxLayout(box)
        on, total = d["uptime"]
        labels = {e.type: e.label or e.type for e in manifest.events}
        counts = ", ".join(f"{labels.get(t, t)}: {n}" for t, n in d["counts"].items()) or "—"
        lay.addWidget(QLabel(f"Uptime: {100 * on / total:.1f}% dari {total} menit tercatat · "
                             f"Event: {counts}" if total else f"Belum ada data kesehatan · "
                             f"Event: {counts}"))
        rows = d["health"]
        if rows:
            p = _plot("FPS proses & waktu per frame")
            xs = [r["minute_ts"] for r in rows]
            for i, (key, name) in enumerate((("proc_fps", "FPS proses"), ("detect_ms", "detect (ms)"),
                                             ("module_ms", "modul (ms)"))):
                gx, gy = with_gaps(xs, [r[key] or 0 for r in rows], 60)
                p.plot(gx, gy, pen=pg.mkPen(PALETTE[i], width=2), connect="finite", name=name)
            lay.addWidget(p)
        return box
