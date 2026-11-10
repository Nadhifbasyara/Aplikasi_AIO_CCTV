"""Halaman Laporan: pratinjau bergaya kartu + ekspor PDF & CSV per periode (Fase 7 §3.6)."""
from __future__ import annotations

from datetime import datetime
from datetime import time as dtime

import pyqtgraph as pg
from PyQt6.QtCore import QDate, QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core.colors import PALETTE
from aio_cctv.gui import theme
from aio_cctv.gui.icons import icon
from aio_cctv.gui.utils import to_qimage
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.reports.csv_export import export_csv
from aio_cctv.reports.pdf_report import (
    CameraInfo,
    CamReport,
    collect,
    header,
    html_to_pdf,
    render_html,
)

TILE_ICONS = {"Menit tercatat": "clock", "Uptime": "clock", "FPS proses rata-rata": "gauge",
              "Detect": "pulse", "Modul": "cpu", "Reconnect": "swap"}


class CheckCombo(QComboBox):
    """Kotak pilihan berisi daftar kamera bercentang; teksnya nama kamera yang dipilih."""
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setModel(QStandardItemModel(self))
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)
        self.view().viewport().installEventFilter(self)
        self.setMinimumWidth(420)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.lineEdit() and ev.type() == QEvent.Type.MouseButtonRelease:
            self.showPopup()
            return True
        if obj is self.view().viewport() and ev.type() == QEvent.Type.MouseButtonRelease:
            item = self.model().itemFromIndex(self.view().indexAt(ev.position().toPoint()))
            if item is not None:                    # centang/lepas tanpa menutup daftar
                on = item.checkState() == Qt.CheckState.Checked
                item.setCheckState(Qt.CheckState.Unchecked if on else Qt.CheckState.Checked)
                self._update_text()
                self.changed.emit()
            return True
        return super().eventFilter(obj, ev)

    def set_items(self, items: list[tuple[str, str]], checked: set[str] | None) -> None:
        self.model().clear()
        for cid, name in items:
            it = QStandardItem(name)
            it.setData(cid, Qt.ItemDataRole.UserRole)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if checked is None or cid in checked
                             else Qt.CheckState.Unchecked)
            self.model().appendRow(it)
        self._update_text()

    def checked_ids(self) -> list[str]:
        m = self.model()
        return [m.item(i).data(Qt.ItemDataRole.UserRole) for i in range(m.rowCount())
                if m.item(i).checkState() == Qt.CheckState.Checked]

    def _update_text(self) -> None:
        m = self.model()
        names = [m.item(i).text() for i in range(m.rowCount())
                 if m.item(i).checkState() == Qt.CheckState.Checked]
        self.lineEdit().setText(", ".join(names) if names else "(pilih kamera)")
        self.lineEdit().setCursorPosition(0)

    def hidePopup(self) -> None:
        super().hidePopup()
        self._update_text()


# ------------------------------------------------------------------ potongan tampilan
def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 10, 14, 14)
    lay.setSpacing(8)
    h = QLabel(title)
    h.setObjectName("sectionTitle")
    lay.addWidget(h)
    return f, lay


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{theme.MUTED};")
    lbl.setWordWrap(True)
    return lbl


def _tile(label: str, value: str) -> QFrame:
    f = QFrame()
    f.setObjectName("tile")
    f.setMinimumWidth(130)
    lay = QHBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 10)
    ico = QLabel()
    ico.setPixmap(icon(TILE_ICONS.get(label, "analytics"), theme.MUTED).pixmap(24, 24))
    lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignVCenter)
    col = QVBoxLayout()
    col.setSpacing(0)
    col.addWidget(QLabel(label))
    v = QLabel(value)
    v.setObjectName("tileValue")
    col.addWidget(v)
    lay.addLayout(col, 1)
    return f


def _table(head: list[str], rows: list[list]) -> QWidget:
    if not rows:
        return _muted("Tidak ada data.")
    t = QTableWidget(len(rows), len(head))
    t.setObjectName("plain")
    t.setHorizontalHeaderLabels(head)
    t.verticalHeader().hide()
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            t.setItem(r, c, QTableWidgetItem(str(val)))
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    t.resizeRowsToContents()
    t.setFixedHeight(hh.height() + sum(t.rowHeight(i) for i in range(len(rows))) + 4)
    return t


def _hour_chart(per_hour: dict[str, list[int]]) -> pg.PlotWidget:
    p = pg.PlotWidget()
    p.setBackground(None)
    p.setMouseEnabled(False, False)
    p.hideButtons()
    p.setMenuEnabled(False)
    p.setFixedHeight(190)
    p.setStyleSheet("border:none;")
    p.showGrid(y=True, alpha=0.15)
    n = max(1, len(per_hour))
    w = 0.8 / n
    if n > 1:
        p.addLegend(offset=(5, 5))
    for i, (label, hours) in enumerate(per_hour.items()):
        xs = [h - 0.4 + w * (i + 0.5) for h in range(24)]
        bar = pg.BarGraphItem(x=xs, height=hours, width=w * 0.9, brush=PALETTE[(i + 1) % len(PALETTE)],
                              pen=pg.mkPen(None), name=label)
        p.addItem(bar)
    p.getAxis("bottom").setTicks([[(h, f"{h:02d}") for h in range(0, 24, 3)]])
    p.setXRange(-0.6, 23.6, padding=0)
    return p


def _camera_section(r: CamReport) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 8, 0, 0)
    lay.setSpacing(10)
    t = QLabel(r.info.name)
    t.setObjectName("camTitle")
    lay.addWidget(t)
    m = r.info.manifest
    if m is None:
        lay.addWidget(_muted("Modul kamera ini tidak terpasang; metrik tidak bisa diberi label."))
        return w
    sub = QLabel(f"Modul: <b>{m.name}</b> <span style='color:{theme.MUTED}'>"
                 f"({m.id} {m.version} · {', '.join(m.authors) or '-'})</span>")
    lay.addWidget(sub)

    card, cl = _card("Kinerja Kamera")
    tiles = QGridLayout()
    tiles.setSpacing(10)
    for i, (k, v) in enumerate(r.perf):
        tiles.addWidget(_tile(k, v), i // 6, i % 6)
    cl.addLayout(tiles)
    lay.addWidget(card)

    card, cl = _card("Metrik")
    cl.addWidget(_table(["Metrik", "Zona", "Nilai", "Agregasi", "Maks"], r.metrics))
    lay.addWidget(card)

    row = QHBoxLayout()
    row.setSpacing(10)
    card, cl = _card("Event")
    if r.events:
        for label, n in r.events:
            pill = QLabel(f"{label}: {n}")
            pill.setProperty("pill", "gray")
            cl.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft)
    else:
        cl.addWidget(_muted("Tidak ada event."))
    cl.addStretch()
    row.addWidget(card, 2)

    card, cl = _card("Event per jam")
    busy = sorted(((n, h, label) for label, hours in r.per_hour.items()
                   for h, n in enumerate(hours) if n), reverse=True)[:5]
    body = QHBoxLayout()
    top = QVBoxLayout()
    top.setSpacing(4)
    if busy:
        top.addWidget(_muted("Jam tersibuk"))
        for n, h, label in busy:
            pill = QLabel(f"{label} · {h:02d}:00  ({n})")
            pill.setProperty("pill", "gray")
            top.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft)
        top.addStretch()
        body.addLayout(top)
        body.addWidget(_hour_chart(r.per_hour), 1)
    else:
        body.addWidget(_muted("Tidak ada event."))
    cl.addLayout(body)
    row.addWidget(card, 5)
    lay.addLayout(row)

    if r.stats:
        card, cl = _card("Durasi & nilai event")
        cl.addWidget(_table(["Event · field", "Zona", "Jumlah", "Rata-rata", "Median", "Maks"],
                            r.stats))
        lay.addWidget(card)

    row = QHBoxLayout()
    row.setSpacing(10)
    card, cl = _card("Heatmap")
    if r.heat is not None:
        img = QLabel()
        img.setPixmap(QPixmap.fromImage(to_qimage(r.heat)).scaledToWidth(
            360, Qt.TransformationMode.SmoothTransformation))
        cl.addWidget(img)
    else:
        cl.addWidget(_muted("Tidak ada data heatmap."))
    cl.addStretch()
    row.addWidget(card, 2)
    card, cl = _card(f"Alert ({len(r.alerts)})")
    cl.addWidget(_table(["Waktu", "Aturan", "Tingkat", "Pesan"], r.alerts[:15]))
    if len(r.alerts) > 15:
        cl.addWidget(_muted(f"…dan {len(r.alerts) - 15} lainnya (lengkap di PDF)."))
    cl.addStretch()
    row.addWidget(card, 5)
    lay.addLayout(row)
    return w


class ReportsPage(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._reports: list[CamReport] = []
        self._range = (0.0, 0.0)
        self._shown_once = False

        self.cmb_cams = CheckCombo()
        self.date_from = QDateEdit(calendarPopup=True)
        self.date_to = QDateEdit(calendarPopup=True)
        for d in (self.date_from, self.date_to):
            d.setDisplayFormat("dd-MM-yyyy")
        self.date_to.setDate(QDate.currentDate())
        self.date_from.setDate(QDate.currentDate().addDays(-6))

        def btn(text, ico, obj):
            b = QPushButton(icon(ico, "#FFFFFF"), f" {text}")
            b.setObjectName(obj)
            b.setIconSize(QSize(16, 16))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            return b

        self.btn_preview = btn("Pratinjau", "eye", "primary")
        self.btn_pdf = btn("Ekspor PDF…", "pdf", "red")
        self.btn_csv = btn("Ekspor CSV…", "table", "success")
        self.btn_preview.clicked.connect(lambda: self.preview())
        self.btn_pdf.clicked.connect(self.export_pdf)
        self.btn_csv.clicked.connect(self.export_csv)
        self.lbl_status = _muted("")

        box, form = _card("Isi Laporan")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.addWidget(QLabel("Kamera"), 0, 0)
        grid.addWidget(self.cmb_cams, 0, 1)
        period = QHBoxLayout()
        period.addWidget(self.date_from)
        period.addWidget(QLabel("s.d."))
        period.addWidget(self.date_to)
        period.addStretch()
        grid.addWidget(QLabel("Periode"), 1, 0)
        grid.addLayout(period, 1, 1)
        grid.setColumnStretch(2, 1)
        form.addLayout(grid)
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        bar = QHBoxLayout()
        for b in (self.btn_preview, self.btn_pdf, self.btn_csv):
            bar.addWidget(b)
        bar.addSpacing(10)
        bar.addWidget(self.lbl_status, 1)

        # lembar laporan
        self.sheet = QFrame()
        self.sheet.setObjectName("sheet")
        self.sheet_lay = QVBoxLayout(self.sheet)
        self.sheet_lay.setContentsMargins(18, 14, 18, 18)
        self.sheet_lay.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.sheet)

        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addLayout(bar)
        lay.addWidget(scroll, 1)
        manager.cameras_changed.connect(self._reload_cameras)
        self._reload_cameras()
        self._placeholder("Pilih kamera dan periode, lalu klik “Pratinjau”.")

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._shown_once:                     # pratinjau otomatis saat pertama dibuka
            self._shown_once = True
            if self.cmb_cams.checked_ids():
                self.preview()

    def _reload_cameras(self) -> None:
        checked = set(self.cmb_cams.checked_ids()) if self.cmb_cams.model().rowCount() else None
        cams = sorted(self.manager.cameras.values(), key=lambda c: c.name.lower())
        self.cmb_cams.set_items([(c.id, c.name) for c in cams], checked)

    def _selection(self) -> tuple[list[CameraInfo], float, float]:
        cams = []
        for cid in self.cmb_cams.checked_ids():
            cam = self.manager.cameras.get(cid)
            if cam is None:
                continue
            profile = self.manager.profile(cam)
            info = self.manager.modules.get(profile.module.module_id)
            cams.append(CameraInfo(cam.id, cam.name, info.manifest if info else None,
                                   profile.region_names))
        start = datetime.combine(self.date_from.date().toPyDate(), dtime.min).timestamp()
        end = datetime.combine(self.date_to.date().toPyDate(), dtime.max).timestamp()
        return cams, start, end

    def _busy(self, on: bool, text: str = "") -> None:
        for b in (self.btn_preview, self.btn_pdf, self.btn_csv):
            b.setEnabled(not on)
        self.lbl_status.setText(text)

    # ---------------------------------------------------------------- lembar
    def _clear_sheet(self) -> None:
        while self.sheet_lay.count():
            item = self.sheet_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _placeholder(self, text: str) -> None:
        self._clear_sheet()
        lbl = _muted(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sheet_lay.addStretch()
        self.sheet_lay.addWidget(lbl)
        self.sheet_lay.addStretch()

    def _render(self) -> None:
        self._clear_sheet()
        title = QLabel("Laporan Analitik CCTV")
        title.setObjectName("reportTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        period = _muted(header(*self._range))
        period.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sheet_lay.addWidget(title)
        self.sheet_lay.addWidget(period)
        for i, r in enumerate(self._reports):
            if i:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet(f"color:{theme.SURFACE_2};")
                self.sheet_lay.addSpacing(8)
                self.sheet_lay.addWidget(sep)
            self.sheet_lay.addWidget(_camera_section(r))
        self.sheet_lay.addStretch()

    # ----------------------------------------------------------------- aksi
    def preview(self, then=None) -> None:
        cams, start, end = self._selection()
        if not cams:
            self.lbl_status.setText("Pilih minimal satu kamera.")
            return
        if start > end:
            self.lbl_status.setText("Tanggal awal setelah tanggal akhir.")
            return
        self._busy(True, "Menyusun laporan…")
        self.manager.storage.flush(timeout=3)        # data menit terakhir ikut masuk

        def done(reports):
            self._reports, self._range = reports, (start, end)
            self._busy(False, f"Pratinjau siap · {datetime.now():%H:%M:%S}")
            self._render()
            if then:
                then()

        run_async(collect, self.manager.db_path, cams, start, end, on_done=done,
                  on_failed=lambda m: self._busy(False, f"Gagal: {m}"))

    def export_pdf(self) -> None:
        default = f"laporan-aio-cctv-{datetime.now():%Y%m%d}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Simpan laporan PDF", default, "PDF (*.pdf)")
        if not path:
            return

        def write():
            from aio_cctv import __version__
            html, images = render_html(self._reports, *self._range, __version__)
            html_to_pdf(html, path, images)
            self.lbl_status.setText(f"PDF tersimpan: {path}")

        self.preview(then=write)

    def export_csv(self) -> None:
        cams, start, end = self._selection()
        if not cams:
            self.lbl_status.setText("Pilih minimal satu kamera.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Pilih folder tujuan CSV")
        if not folder:
            return
        self.manager.storage.flush(timeout=3)
        self._busy(True, "Mengekspor CSV…")
        run_async(export_csv, self.manager.db_path, folder, [c.id for c in cams], start, end,
                  on_done=lambda files: self._busy(False, f"{len(files)} berkas CSV tersimpan di "
                                                          f"{folder}"),
                  on_failed=lambda m: self._busy(False, f"Gagal: {m}"))
