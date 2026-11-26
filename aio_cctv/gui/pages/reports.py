"""Halaman Laporan: pratinjau bergaya kartu + ekspor PDF & CSV per periode (Fase 7 §3.6)."""
from __future__ import annotations

from datetime import datetime
from datetime import time as dtime
from pathlib import Path

import cv2
import pyqtgraph as pg
from PyQt6.QtCore import QDate, QEvent, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap, QStandardItem, QStandardItemModel
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
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
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
    html_to_pdf,
    render_html,
)

# kinerja kamera: label data (pdf_report.collect) -> (judul tile, ikon, satuan tambahan)
PERF_LABELS = {"Menit tercatat": ("Menit Tercatat", "clock", " mnt"),
               "Uptime": ("Waktu Aktif (Uptime)", "clock", ""),
               "FPS proses rata-rata": ("Rerata Proses", "gauge", " FPS"),
               "Detect": ("Waktu Deteksi", "pulse", ""),
               "Modul": ("Waktu Modul", "cpu", ""),
               "Reconnect": ("Koneksi Ulang", "swap", " kali")}
AGG_TEXT = {"avg": "Rata-rata", "max": "Maksimum", "last": "Total Akhir", "sum": "Total"}
FIELD_TEXT = {"duration_s": "Durasi", "dwell_s": "Durasi", "wait_s": "Waktu tunggu"}


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
        self.setMinimumWidth(120)            # lebar diatur panel kiri laporan

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
    f.setMinimumWidth(110)
    lay = QHBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 10)
    ico = QLabel()
    ico.setPixmap(icon(PERF_LABELS.get(label, ("", "analytics", ""))[1], theme.MUTED)
                  .pixmap(22, 22))
    lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignVCenter)
    col = QVBoxLayout()
    col.setSpacing(0)
    title, _, unit = PERF_LABELS.get(label, (label, "", ""))
    lbl = QLabel(title)
    lbl.setWordWrap(True)                          # label panjang turun baris, tidak terpotong
    lbl.setStyleSheet(f"color:{theme.MUTED};")
    col.addWidget(lbl)
    v = QLabel(value + unit if value not in ("-", "") else value)
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


def _num(n) -> str:
    """Angka bulat gaya Indonesia: 1038 -> "1.038"."""
    return f"{int(n):,}".replace(",", ".")


class TileGrid(QWidget):
    """Kotak kinerja yang menyesuaikan lebar: 6 sebaris bila lega, 3×2 atau 2×3 bila sempit,
    supaya label tidak terpecah 2–3 baris saat jendela tidak dimaksimalkan."""
    MIN_W = 185                                   # lebar minimum satu kotak agar label 1 baris

    def __init__(self, tiles: list[QWidget]):
        super().__init__()
        self.tiles = tiles
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self._cols = 0
        self._arrange(len(tiles))

    def _arrange(self, cols: int) -> None:
        cols = max(1, min(len(self.tiles), cols))
        if cols == self._cols:
            return
        # pilih kolom yang membagi rata (6 → 6/3/2) supaya tidak ada baris yang bolong
        n = len(self.tiles)
        while cols > 1 and n % cols:
            cols -= 1
        self._cols = cols
        for t in self.tiles:
            self.grid.removeWidget(t)
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for i, t in enumerate(self.tiles):
            self.grid.addWidget(t, i // cols, i % cols)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._arrange((self.width() + 8) // (self.MIN_W + 8))


class FitImage(QLabel):
    """Gambar selebar wadahnya (rasio tetap, tinggi maks MAX_H); ikut berubah saat diubah ukuran."""
    MAX_H = 420

    def __init__(self, pix: QPixmap):
        super().__init__()
        self._pix = pix
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        w = max(1, self.width())
        h = min(self.MAX_H, round(w * self._pix.height() / max(1, self._pix.width())))
        scaled = self._pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)
        if self.height() != scaled.height():
            self.setFixedHeight(scaled.height())


def _long_units(text: str) -> str:
    """"7 mnt 03 dtk" -> "7 mnt 03 detik" (tampilan laporan lebih mudah dibaca)."""
    return text.replace(" dtk", " detik")


def _section_title(text: str) -> QWidget:
    """Judul bagian laporan dengan garis bawah tipis, seperti dokumen."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 10, 0, 2)
    lay.setSpacing(4)
    t = QLabel(text)
    t.setStyleSheet("font-size:16px; font-weight:bold;")
    w.setProperty("tocTitle", text)               # untuk panel Daftar Isi
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color:{theme.BORDER_STRONG};")
    lay.addWidget(t)
    lay.addWidget(line)
    return w


def _camera_section(r: CamReport) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 8, 0, 0)
    lay.setSpacing(10)
    m = r.info.manifest
    head = QLabel(f"Kamera: <b>{r.info.name}</b><br>"
                  f"Modul: <b>{m.name if m else '(tidak terpasang)'}</b>")
    head.setProperty("tocCamera", r.info.name)
    lay.addWidget(head)
    if m is None:
        lay.addWidget(_muted("Modul kamera ini tidak terpasang; metrik tidak bisa diberi label."))
        return w

    lay.addWidget(_section_title("Kinerja Kamera"))
    lay.addWidget(TileGrid([_tile(k, v) for k, v in r.perf]))

    lay.addWidget(_section_title("Ringkasan Aktivitas (Metrik)"))
    rows = [[name, "Semua Area" if zone == "(kamera)" else zone, val,
             AGG_TEXT.get(agg, agg), mx] for name, zone, val, agg, mx in r.metrics]
    t = _table(["Metrik", "Zona / Area", "Nilai", "Jenis Hitung", "Batas Maksimal"], rows)
    if isinstance(t, QTableWidget):
        t.horizontalHeaderItem(3).setToolTip("Cara modul merangkum metrik (agregasi)")
    lay.addWidget(t)

    lay.addWidget(_section_title("Data Kejadian & Waktu Terpadat"))
    row = QHBoxLayout()
    row.setSpacing(10)
    card, cl = _card("Jumlah Kejadian & Jam Tersibuk")
    if r.events:
        for label, n in r.events:
            cl.addWidget(QLabel(f"{label}: <b>{_num(n)}</b>"))
    else:
        cl.addWidget(_muted("Tidak ada kejadian pada periode ini."))
    totals = [sum(hours[h] for hours in r.per_hour.values()) for h in range(24)]
    busy = sorted(((n, h) for h, n in enumerate(totals) if n), reverse=True)[:3]
    if busy:
        jt = QLabel("Jam Tersibuk")
        jt.setStyleSheet("font-size:14px; font-weight:bold; margin-top:10px;")
        cl.addWidget(jt)
        for n, h in busy:
            cl.addWidget(QLabel(f"{h:02d}:00 – {(h + 1) % 24:02d}:00  ({_num(n)} kejadian)"))
    cl.addStretch()
    row.addWidget(card, 1)
    card, cl = _card("Aktivitas per Jam")
    if any(totals):
        cl.addWidget(_hour_chart(r.per_hour))
    else:
        cl.addWidget(_muted("Tidak ada kejadian pada periode ini."))
    row.addWidget(card, 1)
    lay.addLayout(row)

    if r.stats:
        lay.addWidget(_section_title("Durasi & Nilai Kejadian"))
        rows = []
        for name, zone, n, avg, med, mx in r.stats:
            ev, _, field = name.partition(" · ")
            rows.append([f"{ev} • {FIELD_TEXT.get(field, field)}",
                         "Semua Area" if zone == "(kamera)" else zone, f"{_num(n)} kali",
                         avg, med, mx])
        rows = [[_long_units(c) if isinstance(c, str) else c for c in row] for row in rows]
        lay.addWidget(_table(["Aktivitas", "Zona", "Jumlah", "Rerata", "Median", "Terlama"],
                             rows))

    row = QHBoxLayout()
    row.setSpacing(10)
    card, cl = _card("Heatmap Aktivitas")
    card.setProperty("tocTitle", "Heatmap Aktivitas")      # ikut Daftar Isi
    if r.heat is not None:
        cl.addWidget(FitImage(QPixmap.fromImage(to_qimage(r.heat))))
    else:
        cl.addWidget(_muted("Tidak ada data heatmap."))
    cl.addStretch()
    row.addWidget(card, 1)
    card, cl = _card(f"Daftar Alert ({len(r.alerts)})" if r.alerts else "Daftar Alert")
    card.setProperty("tocTitle", "Daftar Alert")
    if r.alerts:
        cl.addWidget(_table(["Waktu", "Aturan", "Tingkat", "Pesan"], r.alerts[:15]))
        if len(r.alerts) > 15:
            cl.addWidget(_muted(f"…dan {len(r.alerts) - 15} lainnya (lengkap di PDF)."))
    else:
        ok = QHBoxLayout()
        ok.setSpacing(6)
        ico = QLabel()
        ico.setPixmap(icon("check_circle", theme.OK).pixmap(16, 16))
        ok.addWidget(ico)
        ok.addWidget(QLabel("Tidak ada alert (sistem berjalan normal)."))
        ok.addStretch()
        cl.addLayout(ok)
    cl.addStretch()
    row.addWidget(card, 1)
    lay.addLayout(row)
    return w


class ReportsPage(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._reports: list[CamReport] = []
        self._bg: dict = {}                          # cam_id -> citra kamera terakhir
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

        self.btn_preview = btn("Lihat Pratinjau", "eye", "primary")
        self.btn_pdf = btn("Ekspor ke PDF", "pdf", "red")
        self.btn_csv = btn("Ekspor ke CSV", "table", "success")
        self.btn_preview.clicked.connect(lambda: self.preview())
        self.btn_pdf.clicked.connect(self.export_pdf)
        self.btn_csv.clicked.connect(self.export_csv)
        self.lbl_status = _muted("")

        # --- panel kiri: isi laporan, periode cepat, aksi, riwayat ekspor
        box, form = _card("Isi Laporan")
        form.addWidget(QLabel("Kamera"))
        self.cmb_cams.setMinimumContentsLength(10)  # nama kamera panjang tidak melebarkan panel
        self.cmb_cams.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_cams.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        form.addWidget(self.cmb_cams)
        form.addWidget(QLabel("Periode Tanggal"))
        period = QHBoxLayout()
        for d in (self.date_from, self.date_to):  # dua tanggal harus muat selebar panel kiri
            d.setMinimumWidth(110)
            d.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        period.addWidget(self.date_from, 1)
        period.addWidget(QLabel("s.d."))
        period.addWidget(self.date_to, 1)
        form.addLayout(period)
        quick = QGridLayout()
        quick.setSpacing(6)
        for i, (label, days) in enumerate((("Hari ini", 0), ("7 hari", 6), ("30 hari", 29),
                                           ("Bulan ini", -1))):
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, d=days: self._quick_range(d))
            quick.addWidget(b, i // 2, i % 2)
        form.addLayout(quick)
        for b in (self.btn_preview, self.btn_pdf, self.btn_csv):
            form.addWidget(b)
        form.addWidget(self.lbl_status)

        hist_card, hl = _card("Riwayat Ekspor")
        self.lst_history = QListWidget()
        self.lst_history.setObjectName("plainList")
        self.lst_history.setStyleSheet("QListWidget { background:transparent; border:none; }")
        self.lst_history.setToolTip("Klik ganda untuk membuka berkas/folder")
        self.lst_history.itemDoubleClicked.connect(self._open_history)
        hl.addWidget(self.lst_history, 1)
        self.history_empty = QWidget()
        el = QVBoxLayout(self.history_empty)
        el.addStretch()
        big = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        big.setPixmap(icon("report", theme.MUTED).pixmap(56, 56))
        txt = QLabel("Belum ada riwayat ekspor", alignment=Qt.AlignmentFlag.AlignCenter)
        txt.setStyleSheet(f"color:{theme.MUTED};")
        el.addWidget(big)
        el.addWidget(txt)
        el.addStretch(2)
        hl.addWidget(self.history_empty, 1)
        self._load_history()

        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(box)
        left.addWidget(hist_card, 1)
        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setFixedWidth(340)
        left.setContentsMargins(0, 0, 0, 0)

        # --- panel kanan: daftar isi (lompat ke bagian)
        toc_card, tl = _card("Daftar Isi")
        self.lst_toc = QListWidget()
        self.lst_toc.setStyleSheet(
            "QListWidget { background:transparent; border:none; }"
            "QListWidget::item { padding:5px 4px; border-radius:4px; }"
            f"QListWidget::item:selected, QListWidget::item:hover {{ background:{theme.SURFACE_2};"
            f" color:{theme.TEXT}; }}")
        self.lst_toc.itemClicked.connect(self._jump_to)
        self.lst_toc.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.lst_toc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lst_toc.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lst_toc.setWordWrap(True)
        tl.addWidget(self.lst_toc)

        # Info Laporan: kapan dibuat, periode, kelengkapan data
        info_card, il = _card("Info Laporan")
        self.lbl_info = QLabel("Belum ada pratinjau.")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setTextFormat(Qt.TextFormat.RichText)
        il.addWidget(self.lbl_info)

        # Catatan Laporan: ikut dicetak di PDF
        note_card, nl = _card("Catatan Laporan")
        self.ed_notes = QPlainTextEdit()
        self.ed_notes.setPlaceholderText("Tulis catatan/kesimpulan, mis. “Kamera mati 22-11 "
                                         "karena listrik padam”. Ikut dicetak di PDF.")
        from aio_cctv.gui.utils import qsettings
        self.ed_notes.setPlainText(qsettings().value("report_notes", "", type=str) or "")
        self.ed_notes.textChanged.connect(
            lambda: qsettings().setValue("report_notes", self.ed_notes.toPlainText()))
        nl.addWidget(self.ed_notes, 1)
        hint = _muted("Disimpan otomatis.")
        nl.addWidget(hint)

        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(10)
        side.addWidget(toc_card)
        side.addWidget(info_card)
        side.addWidget(note_card, 1)
        self.toc_w = QWidget()
        self.toc_w.setLayout(side)
        self.toc_w.setFixedWidth(300)

        # lembar laporan
        self.sheet = QFrame()
        self.sheet.setObjectName("sheet")
        self.sheet_lay = QVBoxLayout(self.sheet)
        self.sheet_lay.setContentsMargins(18, 14, 18, 18)
        self.sheet_lay.setSpacing(6)
        center = QWidget()
        cl = QHBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 10)
        cl.addWidget(self.sheet, 1)                # lembar memenuhi area tengah
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(center)
        self.scroll = scroll

        lay = QHBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(left_w)
        lay.addWidget(scroll, 1)
        lay.addWidget(self.toc_w)
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
        self.cmb_cams.set_items([(c.id, f"{c.name} ({c.location})" if c.location else c.name)
                                 for c in cams], checked)

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

    def _with_background(self, cam_id: str, heat):
        """Tumpuk heatmap di atas citra kamera terakhir (FrameHub saat kamera berjalan; disimpan
        untuk sesi ini). Mode anonim: citra yang sudah diblur. Tanpa citra: heatmap apa adanya."""
        frame = self.manager.frames.get(cam_id)
        if frame is not None:
            self._bg[cam_id] = (frame.annotated if frame.blurred else frame.raw).copy()
        bg = self._bg.get(cam_id)
        if heat is None or bg is None:
            return heat
        h, w = heat.shape[:2]
        return cv2.addWeighted(heat, 0.5, cv2.resize(bg, (w, h)), 0.5, 0)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.toc_w.setVisible(self.width() > 1500)    # layar sempit: lembar laporan didahulukan

    # ------------------------------------------------------------ panel samping
    def _quick_range(self, days: int) -> None:
        today = QDate.currentDate()
        start = QDate(today.year(), today.month(), 1) if days < 0 else today.addDays(-days)
        self.date_from.setDate(start)
        self.date_to.setDate(today)
        self.preview()

    def _build_toc(self) -> None:
        self.lst_toc.clear()
        # hanya isi lembar yang SEKARANG ada di layout (bagian lama menunggu deleteLater)
        current = []
        for i in range(self.sheet_lay.count()):
            if (top := self.sheet_lay.itemAt(i).widget()) is not None:
                current += [top, *top.findChildren(QWidget)]
        multi = len(self._reports) > 1
        for w in current:
            if (cam := w.property("tocCamera")):
                if not multi:                      # satu kamera: cukup daftar bagian
                    continue
                item = QListWidgetItem(icon("camera", theme.TEXT), cam)
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            elif (title := w.property("tocTitle")):
                item = QListWidgetItem(icon("list", theme.MUTED), title)
            else:
                continue
            item.setData(Qt.ItemDataRole.UserRole, w)
            self.lst_toc.addItem(item)
        rows = self.lst_toc.count()
        # tinggi = jumlah semua baris yang sebenarnya (huruf besar di layar pengguna ikut terhitung)
        h = sum(self.lst_toc.sizeHintForRow(i) for i in range(rows)) + 2 * self.lst_toc.frameWidth()
        self.lst_toc.setFixedHeight(max(40, h + 6))
        self._update_info()

    def _update_info(self) -> None:
        """Ringkasan isi laporan + kelengkapan data (menit tercatat / menit dalam periode)."""
        if not self._reports:
            self.lbl_info.setText("Belum ada pratinjau.")
            return
        start, end = self._range
        days = max(1, round((end - start) / 86400))
        period_min = max(1.0, (min(end, datetime.now().timestamp()) - start) / 60)
        lines = [f"Dibuat: <b>{datetime.now():%d-%m-%Y %H:%M}</b>",
                 f"Periode: <b>{days} hari</b> · {len(self._reports)} kamera"]
        for r in self._reports:
            recorded = next((v for k, v in r.perf if k == "Menit tercatat"), "0")
            try:
                pct = min(100.0, 100 * float(recorded) / period_min)
            except ValueError:
                pct = 0.0
            color = theme.OK if pct >= 80 else theme.WARN if pct >= 30 else theme.BAD
            lines.append(f"{r.info.name}: data <span style='color:{color}'><b>{pct:.1f}%</b></span>"
                         f" periode ({recorded} mnt tercatat)")
        self.lbl_info.setText("<br>".join(lines))
        self.lbl_info.setToolTip("Kelengkapan data = menit yang benar-benar tercatat dibanding "
                                 "seluruh menit dalam periode (sampai sekarang). Rendah berarti "
                                 "kamera/aplikasi lebih sering mati pada periode itu.")

    def _jump_to(self, item: QListWidgetItem) -> None:
        w = item.data(Qt.ItemDataRole.UserRole)
        if w is None:
            return
        y = w.mapTo(self.scroll.widget(), w.rect().topLeft()).y()
        self.scroll.verticalScrollBar().setValue(max(0, y - 8))

    def _load_history(self) -> None:
        from aio_cctv.gui.utils import qsettings
        self._history = list(qsettings().value("report_history", [], type=list) or [])
        self.lst_history.clear()
        for entry in self._history:
            when, kind, path = (entry.split("|", 2) + ["", ""])[:3]
            item = QListWidgetItem(icon("pdf" if kind == "PDF" else "table", theme.MUTED),
                                   f"{Path(path).name}\n{when}")
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.lst_history.addItem(item)
        if hasattr(self, "history_empty"):
            self.history_empty.setVisible(not self._history)
        self.lst_history.setVisible(bool(self._history))

    def _add_history(self, kind: str, path: str) -> None:
        from aio_cctv.gui.utils import qsettings
        entry = f"{datetime.now():%d-%m-%Y %H:%M}|{kind}|{path}"
        self._history = [entry, *[h for h in self._history if not h.endswith(f"|{path}")]][:12]
        qsettings().setValue("report_history", self._history)
        self._load_history()

    def _open_history(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif path:
            self.lbl_status.setText(f"Berkas tidak ditemukan lagi: {path}")

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
        title = QLabel("LAPORAN ANALITIK CCTV")
        title.setObjectName("reportTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        start, end = self._range
        period = QLabel(f"Periode: {datetime.fromtimestamp(start):%d-%m-%Y} s.d. "
                        f"{datetime.fromtimestamp(end):%d-%m-%Y}")
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
        self._build_toc()

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
            for r in reports:                        # heatmap di atas citra kamera (juga di PDF)
                r.heat = self._with_background(r.info.id, r.heat)
            self._reports, self._range = reports, (start, end)
            self._busy(False, f"Pratinjau siap • {datetime.now():%H:%M:%S}")
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
            html, images = render_html(self._reports, *self._range, __version__,
                                       notes=self.ed_notes.toPlainText())
            html_to_pdf(html, path, images)
            self.lbl_status.setText(f"PDF tersimpan: {Path(path).name}")
            self.lbl_status.setToolTip(path)
            self._add_history("PDF", path)

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
                  on_done=lambda files: (self._busy(False, f"{len(files)} berkas CSV tersimpan "
                                                           f"di {Path(folder).name}/"),
                                         self.lbl_status.setToolTip(folder),
                                         self._add_history("CSV", folder)),
                  on_failed=lambda m: self._busy(False, f"Gagal: {m}"))
