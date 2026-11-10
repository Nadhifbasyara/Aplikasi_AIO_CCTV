"""Wizard preset (Fase 8 §5): pilih modul & preset → gambar tiap slot → parameter & aturan →
ringkasan. Semua perbedaan antar-jenis usaha datang dari preset modul, bukan dari kode host."""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPolygonItem,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from aio_cctv.alerts.rules import Rule
from aio_cctv.core.colors import PALETTE
from aio_cctv.gui import theme
from aio_cctv.gui.pages.zone_editor import ZoneCanvas
from aio_cctv.gui.utils import to_qimage
from aio_cctv.gui.widgets.module_params import ModuleParamsForm
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.modules.presets import apply_preset, missing_slots
from aio_cctv.sources.snapshot import grab_frame

EMPTY = "__kosong__"


class ModulePage(QWizardPage):
    def __init__(self, wiz: PresetWizard):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Langkah 1 — Modul & preset")
        self.setSubTitle("Pilih modul analitik dan jenis tempat. Preset dibuat oleh pembuat modul.")
        self.cbo_module = QComboBox()
        self.lst = QListWidget()
        self.lbl_desc = QLabel()
        self.lbl_desc.setWordWrap(True)
        self.lbl_warn = QLabel()
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet(f"color:{theme.WARN};")
        form = QFormLayout(self)
        form.addRow("Kamera", QLabel(f"<b>{wiz.cam.name}</b>"))
        form.addRow("Modul", self.cbo_module)
        form.addRow("Preset", self.lst)
        form.addRow("", self.lbl_desc)
        form.addRow("", self.lbl_warn)
        for mid, info in sorted(wiz.manager.modules.items()):
            if info.valid:
                self.cbo_module.addItem(f"{info.manifest.name}  ({mid})", mid)
        self.cbo_module.setCurrentIndex(max(0, self.cbo_module.findData(
            wiz.profile.module.module_id)))
        self.cbo_module.currentIndexChanged.connect(self._fill_presets)
        self.lst.currentRowChanged.connect(self._describe)
        self._fill_presets()

    def _fill_presets(self) -> None:
        info = self.wiz.manager.modules.get(self.cbo_module.currentData())
        self.wiz.manifest = info.manifest if info else None
        self.wiz.presets = info.manifest.load_presets()[0] if info else []
        self.lst.clear()
        for p in self.wiz.presets:
            item = QListWidgetItem(p.label)
            item.setData(Qt.ItemDataRole.UserRole, p.preset_id)
            self.lst.addItem(item)
        item = QListWidgetItem("Kosong — hanya ganti modul, zona/garis lama dipertahankan")
        item.setData(Qt.ItemDataRole.UserRole, EMPTY)
        self.lst.addItem(item)
        current = self.wiz.profile.module.preset_id
        rows = [self.lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.lst.count())]
        self.lst.setCurrentRow(rows.index(current) if current in rows else 0)
        self.completeChanged.emit()

    def _describe(self) -> None:
        p = self.wiz.preset
        self.lbl_desc.setText(p.description if p else "")
        n = len(self.wiz.profile.zones) + len(self.wiz.profile.lines)
        self.lbl_warn.setText(f"Kamera ini sudah punya {n} zona/garis; preset akan "
                              "MENGGANTINYA beserta aturan & dashboard." if p and n else "")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self.wiz.manifest is not None and self.lst.currentRow() >= 0

    def nextId(self) -> int:
        return PresetWizard.DRAW if self.wiz.preset else PresetWizard.PARAMS


class DrawPage(QWizardPage):
    def __init__(self, wiz: PresetWizard):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Langkah 2 — Gambar area")
        self.setSubTitle("Pilih area di kiri, lalu klik di gambar. Zona: klik titik-titik, "
                         "klik-ganda/Enter untuk menutup. Garis: dua klik. Esc membatalkan.")
        self.lst = QListWidget()
        self.lst.setMaximumWidth(280)
        self.lst.currentRowChanged.connect(self._on_slot)
        self.lbl_hint = QLabel()
        self.lbl_hint.setWordWrap(True)
        self.btn_clear = QPushButton("Hapus bentuk area ini")
        self.btn_clear.clicked.connect(self._clear_slot)
        self.btn_snap = QPushButton("Ambil snapshot ulang")
        self.btn_snap.clicked.connect(self.take_snapshot)
        self.canvas = ZoneCanvas()
        self.scene = self.canvas.scene()
        self.pix = self.scene.addPixmap(QPixmap())
        self.canvas.clicked.connect(self._click)
        self.canvas.finish_requested.connect(self._finish)
        self.canvas.cancel_requested.connect(self._cancel)
        self.draft: list[QPointF] = []
        self.draft_items: list = []
        self.items: dict[str, list] = {}
        self.frame_wh = tuple(wiz.profile.frame_size or (1280, 720))

        left = QVBoxLayout()
        left.addWidget(self.lst, 1)
        left.addWidget(self.lbl_hint)
        left.addWidget(self.btn_clear)
        left.addWidget(self.btn_snap)
        lay = QHBoxLayout(self)
        lay.addLayout(left)
        lay.addWidget(self.canvas, 1)

    def initializePage(self) -> None:
        self.wiz.drawn = {}
        for items in self.items.values():
            for it in items:
                self.scene.removeItem(it)
        self.items = {}
        self._refresh_list(0)
        if self.wiz.snapshot is None:
            self.take_snapshot()
        else:
            self._set_background(self.wiz.snapshot)

    # --------------------------------------------------------------- snapshot
    def take_snapshot(self) -> None:
        self.btn_snap.setEnabled(False)
        self.lbl_hint.setText("Mengambil snapshot kamera…")
        uri = self.wiz.manager.source_uri(self.wiz.cam)
        run_async(grab_frame, uri, on_done=self._on_snapshot,
                  on_failed=lambda m: self._on_snapshot(None))

    def _on_snapshot(self, img) -> None:
        self.btn_snap.setEnabled(True)
        if img is not None:
            self.wiz.snapshot = img
        self._set_background(img)
        self._on_slot(self.lst.currentRow())

    def _set_background(self, img) -> None:
        if img is None:
            w, h = self.frame_wh
            blank = QImage(w, h, QImage.Format.Format_RGB888)
            blank.fill(QColor("#202020"))
            pix = QPixmap.fromImage(blank)
        else:
            h, w = img.shape[:2]
            self.frame_wh = (w, h)
            pix = QPixmap.fromImage(to_qimage(img))
        self.pix.setPixmap(pix)
        self.scene.setSceneRect(0, 0, *self.frame_wh)
        self.canvas.fitInView(self.pix, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.canvas.fitInView(self.pix, Qt.AspectRatioMode.KeepAspectRatio)

    # --------------------------------------------------------------- slot
    @property
    def slot(self):
        i = self.lst.currentRow()
        slots = self.wiz.preset.slots if self.wiz.preset else []
        return slots[i] if 0 <= i < len(slots) else None

    def _refresh_list(self, select: int | None = None) -> None:
        row = self.lst.currentRow() if select is None else select
        self.lst.blockSignals(True)
        self.lst.clear()
        for s in self.wiz.preset.slots:
            n = len(self.wiz.drawn.get(s.key, []))
            mark = "✓" if n else ("–" if s.optional else "✗")
            kind = "zona" if s.kind == "zone" else "garis"
            extra = f" ×{n}" if n > 1 else ""
            self.lst.addItem(f"{mark} {s.label or s.key} ({kind}{', opsional' if s.optional else ''})"
                             f"{extra}")
        self.lst.blockSignals(False)
        self.lst.setCurrentRow(max(0, min(row, self.lst.count() - 1)))
        self.completeChanged.emit()

    def _on_slot(self, _row: int = -1) -> None:
        self._cancel()
        s = self.slot
        if s is None:
            return
        more = " Bisa lebih dari satu." if s.multiple else ""
        self.lbl_hint.setText(f"<b>{s.label or s.key}</b><br>{s.hint}{more}")

    def _clear_slot(self) -> None:
        s = self.slot
        if s is None:
            return
        for it in self.items.pop(s.key, []):
            self.scene.removeItem(it)
        self.wiz.drawn.pop(s.key, None)
        self._refresh_list()

    # --------------------------------------------------------------- gambar
    def _click(self, pos: QPointF) -> None:
        s = self.slot
        if s is None or self.pix.pixmap().isNull():
            return
        if self.wiz.drawn.get(s.key) and not s.multiple and not self.draft:
            self._clear_slot()                     # slot tunggal: gambar ulang menggantikan
        self.draft.append(pos)
        self.canvas.drafting = True
        self._redraw_draft()
        if s.kind == "line" and len(self.draft) == 2:
            self._finish()

    def _redraw_draft(self) -> None:
        for it in self.draft_items:
            self.scene.removeItem(it)
        self.draft_items = []
        pen = QPen(QColor("#00E676"), 2, Qt.PenStyle.DashLine)
        if len(self.draft) > 1:
            it = QGraphicsPolygonItem(QPolygonF(self.draft))
            it.setPen(pen)
            self.scene.addItem(it)
            self.draft_items.append(it)
        for p in self.draft:
            dot = QGraphicsEllipseItem(p.x() - 4, p.y() - 4, 8, 8)
            dot.setBrush(QBrush(QColor("#00E676")))
            self.scene.addItem(dot)
            self.draft_items.append(dot)

    def _cancel(self) -> None:
        self.draft = []
        self.canvas.drafting = False
        self._redraw_draft()

    def _finish(self) -> None:
        s = self.slot
        pts = list(self.draft)
        if len(pts) > 1 and (pts[-1] - pts[-2]).manhattanLength() < 8:   # titik kembar klik-ganda
            pts.pop()
        need = 3 if s and s.kind == "zone" else 2
        if s is None or len(pts) < need:
            return
        w, h = self.frame_wh
        norm = [(min(max(p.x() / (w - 1), 0.0), 1.0), min(max(p.y() / (h - 1), 0.0), 1.0))
                for p in pts[:need if s.kind == "line" else None]]
        self.wiz.drawn.setdefault(s.key, []).append(norm)
        color = QColor(PALETTE[sum(len(v) for v in self.wiz.drawn.values()) % len(PALETTE)])
        if s.kind == "zone":
            it = QGraphicsPolygonItem(QPolygonF(pts))
            fill = QColor(color)
            fill.setAlpha(60)
            it.setBrush(QBrush(fill))
        else:
            it = QGraphicsLineItem(pts[0].x(), pts[0].y(), pts[1].x(), pts[1].y())
        it.setPen(QPen(color, 3))
        self.scene.addItem(it)
        self.items.setdefault(s.key, []).append(it)
        self._cancel()
        self._refresh_list()

    def isComplete(self) -> bool:
        return bool(self.wiz.preset) and not missing_slots(self.wiz.preset, self.wiz.drawn)


class ParamsPage(QWizardPage):
    def __init__(self, wiz: PresetWizard):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Langkah 3 — Parameter & aturan alert")
        self.setSubTitle("Nilai awal dari preset. Aturan bisa diubah lagi di halaman Alert.")
        self.form = ModuleParamsForm()
        self.lst_rules = QListWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Parameter modul</b>"))
        lay.addWidget(self.form)
        lay.addWidget(QLabel("<b>Aturan alert</b> (centang = aktif)"))
        lay.addWidget(self.lst_rules, 1)

    def initializePage(self) -> None:
        p = self.wiz.preset
        same_module = self.wiz.manifest.id == self.wiz.profile.module.module_id
        values = p.params if p else (self.wiz.profile.module.params if same_module else {})
        self.form.set_module(self.wiz.manifest, values)
        self.lst_rules.clear()
        rules = [Rule.model_validate(r) for r in p.rules] if p else \
            apply_preset(self.wiz.profile, self.wiz.manifest, None, {}).rules
        for r in rules:
            item = QListWidgetItem(f"{r.describe()} — {r.message}" if r.message else r.describe())
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if r.enabled else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.lst_rules.addItem(item)
        if not rules:
            self.lst_rules.addItem("(tidak ada — aturan bawaan host: kamera offline & modul error)")

    def rules(self) -> list[Rule] | None:
        out = []
        for i in range(self.lst_rules.count()):
            item = self.lst_rules.item(i)
            r = item.data(Qt.ItemDataRole.UserRole)
            if r is not None:
                out.append(r.model_copy(update={"enabled":
                                                item.checkState() == Qt.CheckState.Checked}))
        return out


class SummaryPage(QWizardPage):
    def __init__(self, wiz: PresetWizard):
        super().__init__()
        self.wiz = wiz
        self.setTitle("Langkah 4 — Ringkasan")
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        self.chk_restart = QCheckBox("Jalankan ulang kamera bila sedang berjalan")
        self.chk_restart.setChecked(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self.lbl)
        lay.addStretch()
        lay.addWidget(self.chk_restart)

    def initializePage(self) -> None:
        try:
            self.wiz.result = self.wiz.build()
        except ValueError as e:
            self.wiz.result = None
            self.lbl.setText(f"<span style='color:{theme.BAD}'>{e}</span>")
            return
        r, m = self.wiz.result, self.wiz.manifest
        zones = "".join(f"<li>Zona <b>{z.name}</b> (peran {z.role or '-'})</li>" for z in r.zones)
        lines = "".join(f"<li>Garis <b>{ln.name}</b> (peran {ln.role or '-'})</li>" for ln in r.lines)
        rules = "".join(f"<li>{x.describe()}{'' if x.enabled else ' (nonaktif)'}</li>"
                        for x in r.rules) or "<li>aturan bawaan host</li>"
        self.lbl.setText(
            f"Kamera <b>{self.wiz.cam.name}</b> akan memakai modul <b>{m.name}</b> {m.version}"
            f"{' dengan preset <b>' + self.wiz.preset.label + '</b>' if self.wiz.preset else ''}."
            f"<p>Area:</p><ul>{zones}{lines or ''}</ul><p>Aturan alert:</p><ul>{rules}</ul>"
            f"<p>Parameter yang diubah dari bawaan: {r.module.params or '-'}</p>")

    def isComplete(self) -> bool:
        return self.wiz.result is not None


class PresetWizard(QWizard):
    MODULE, DRAW, PARAMS, SUMMARY = range(4)

    def __init__(self, manager, cam_id: str, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.cam = manager.cameras[cam_id]
        self.profile = manager.profile(self.cam)
        self.manifest = None
        self.presets = []
        self.drawn: dict[str, list] = {}
        self.snapshot: np.ndarray | None = None
        self.result = None
        self.setWindowTitle(f"Wizard preset — {self.cam.name}")
        self.resize(1100, 720)
        self.page_module = ModulePage(self)
        self.page_draw = DrawPage(self)
        self.page_params = ParamsPage(self)
        self.page_summary = SummaryPage(self)
        for pid, page in ((self.MODULE, self.page_module), (self.DRAW, self.page_draw),
                          (self.PARAMS, self.page_params), (self.SUMMARY, self.page_summary)):
            self.setPage(pid, page)
        self.page_module._describe()               # ulangi: saat dibuat, wizard belum lengkap
        self.setButtonText(QWizard.WizardButton.FinishButton, "Simpan")

    @property
    def preset(self):
        page = getattr(self, "page_module", None)   # None selama ModulePage masih dibuat
        item = page.lst.currentItem() if page is not None else None
        pid = item.data(Qt.ItemDataRole.UserRole) if item is not None else EMPTY
        return next((p for p in self.presets if p.preset_id == pid), None)

    def build(self):
        params = self.page_params.form.values(self.manifest)
        out = apply_preset(self.profile, self.manifest, self.preset, self.drawn, params,
                           self.page_draw.frame_wh if self.preset else None)
        rules = self.page_params.rules()
        if self.preset is not None or rules:
            out.rules = rules
        return out

    def accept(self) -> None:
        if self.result is None:
            return
        self.manager.save_profile(self.cam, self.result)
        if self.page_summary.chk_restart.isChecked() and self.manager.is_running(self.cam.id):
            self.manager.stop(self.cam.id)
            self.manager.start(self.cam.id)
        super().accept()

    def reject(self) -> None:
        if self.drawn and QMessageBox.question(
                self, "Batalkan wizard", "Area yang sudah digambar akan hilang. Batalkan?"
        ) != QMessageBox.StandardButton.Yes:
            return
        super().reject()
