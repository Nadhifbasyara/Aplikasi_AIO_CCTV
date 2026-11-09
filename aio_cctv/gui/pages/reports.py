"""Halaman Laporan: ekspor CSV & PDF per periode (Fase 7 §3.6)."""
from __future__ import annotations

import logging
from datetime import datetime
from datetime import time as dtime

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core.paths import PROFILES_DIR
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.reports.csv_export import export_csv
from aio_cctv.reports.pdf_report import CameraInfo, build_report, html_to_pdf
from aio_cctv.zones.models import Profile

log = logging.getLogger(__name__)


class ReportsPage(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._html, self._images = "", {}

        self.lst_cams = QListWidget()
        self.lst_cams.setMaximumHeight(140)
        self.date_from = QDateEdit(calendarPopup=True)
        self.date_to = QDateEdit(calendarPopup=True)
        for d in (self.date_from, self.date_to):
            d.setDisplayFormat("dd-MM-yyyy")
        self.date_to.setDate(QDate.currentDate())
        self.date_from.setDate(QDate.currentDate().addDays(-6))
        self.btn_preview = QPushButton("Pratinjau")
        self.btn_pdf = QPushButton("Ekspor PDF…")
        self.btn_csv = QPushButton("Ekspor CSV…")
        self.btn_preview.clicked.connect(self.preview)
        self.btn_pdf.clicked.connect(self.export_pdf)
        self.btn_csv.clicked.connect(self.export_csv)
        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        self.view = QTextBrowser()

        box = QGroupBox("Isi laporan")
        form = QFormLayout(box)
        form.addRow("Kamera", self.lst_cams)
        period = QHBoxLayout()
        period.addWidget(self.date_from)
        period.addWidget(QLabel("s.d."))
        period.addWidget(self.date_to)
        period.addStretch()
        form.addRow("Periode", period)
        bar = QHBoxLayout()
        for b in (self.btn_preview, self.btn_pdf, self.btn_csv):
            bar.addWidget(b)
        bar.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addLayout(bar)
        lay.addWidget(self.lbl_status)
        lay.addWidget(self.view, 1)
        manager.cameras_changed.connect(self._reload_cameras)
        self._reload_cameras()

    def _reload_cameras(self) -> None:
        checked = {self.lst_cams.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(self.lst_cams.count())
                   if self.lst_cams.item(i).checkState() == Qt.CheckState.Checked}
        self.lst_cams.clear()
        for cam in sorted(self.manager.cameras.values(), key=lambda c: c.name.lower()):
            item = QListWidgetItem(cam.name)
            item.setData(Qt.ItemDataRole.UserRole, cam.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if not checked or cam.id in checked
                               else Qt.CheckState.Unchecked)
            self.lst_cams.addItem(item)

    def _selection(self) -> tuple[list[CameraInfo], float, float]:
        cams = []
        for i in range(self.lst_cams.count()):
            item = self.lst_cams.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            cam = self.manager.cameras.get(item.data(Qt.ItemDataRole.UserRole))
            if cam is None:
                continue
            info = self.manager.module_for(cam)
            path = PROFILES_DIR / f"{cam.profile_id or cam.id}.json"
            names = {}
            try:
                if path.exists():
                    p = Profile.load(path)
                    names = {z.id: z.name for z in p.zones} | {ln.id: ln.name for ln in p.lines}
            except Exception as e:                 # profil rusak: zona tampil sebagai id
                log.warning("profil %s tidak terbaca: %s", path.name, e)
            cams.append(CameraInfo(cam.id, cam.name, info.manifest if info else None, names))
        start = datetime.combine(self.date_from.date().toPyDate(), dtime.min).timestamp()
        end = datetime.combine(self.date_to.date().toPyDate(), dtime.max).timestamp()
        return cams, start, end

    def _busy(self, on: bool, text: str = "") -> None:
        for b in (self.btn_preview, self.btn_pdf, self.btn_csv):
            b.setEnabled(not on)
        self.lbl_status.setText(text)

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

        def done(result):
            self._html, self._images = result
            self._busy(False, "Pratinjau siap.")
            self.view.document().clear()
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QTextDocument

            from aio_cctv.gui.utils import to_qimage
            for name, img in self._images.items():
                self.view.document().addResource(QTextDocument.ResourceType.ImageResource,
                                                 QUrl(name), to_qimage(img))
            self.view.setHtml(self._html)
            if then:
                then()

        run_async(build_report, self.manager.db_path, cams, start, end, on_done=done,
                  on_failed=lambda m: self._busy(False, f"Gagal: {m}"))

    def export_pdf(self) -> None:
        default = f"laporan-aio-cctv-{datetime.now():%Y%m%d}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Simpan laporan PDF", default, "PDF (*.pdf)")
        if not path:
            return

        def write():
            html_to_pdf(self._html, path, self._images)
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
                  on_done=lambda files: self._busy(False, "CSV tersimpan:\n" +
                                                   "\n".join(str(f) for f in files)),
                  on_failed=lambda m: self._busy(False, f"Gagal: {m}"))
