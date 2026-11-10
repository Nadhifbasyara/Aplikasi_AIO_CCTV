"""Halaman Modul: kartu modul terpasang + panel rincian, pasang & uji paket modul (Fase 6 §5.6)."""
import hashlib
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core.paths import BUNDLED_MODULES_DIR, USER_MODULES_DIR
from aio_cctv.gui import theme
from aio_cctv.gui.icons import icon
from aio_cctv.gui.utils import to_qimage
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.modules import registry
from aio_cctv.modules.check import run_checks

TRUST_NOTE = ("Modul menjalankan kode Python di komputer ini. Pasang hanya paket dari tim "
              "atau sumber yang Anda percaya.")
CARD_W = 300                                     # lebar kartu; jumlah kolom menyesuaikan layar
CONTRACT = "docs/kontrak-modul.md"


def _quick_check(path: str) -> tuple[bool, str]:
    """Dijalankan di thread pool: memuat model & kode modul bisa makan beberapa detik."""
    lines: list[str] = []
    ok = run_checks(path, log=lines.append)
    return ok, "\n".join(lines)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pill(text: str, kind: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("pill", kind)
    return lbl


def _icon_label(name: str, color: str = theme.MUTED, size: int = 15) -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(icon(name, color).pixmap(size, size))
    return lbl


def _split_name(name: str) -> tuple[str, str]:
    """"Baseline (ByteTrack + …)" -> ("Baseline", "(ByteTrack + …)")."""
    head, sep, tail = name.partition(" (")
    return (head, f"({tail}") if sep else (name, "")


def _is_bundled(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(BUNDLED_MODULES_DIR.resolve())
    except OSError:
        return False


class ModuleCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, info, cameras: int):
        super().__init__()
        self.info = info
        self.setObjectName("card")
        self.setProperty("selected", False)
        self.setFixedWidth(CARD_W)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        m = info.manifest
        title, subtitle = _split_name(m.name if m else info.path.name)

        head = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("cardTitle")
        t.setWordWrap(True)
        head.addWidget(t, 1)
        if m is not None:
            head.addWidget(_pill(f"Versi {m.version}", "gray"), 0, Qt.AlignmentFlag.AlignTop)
        else:
            head.addWidget(_pill("Rusak", "red"), 0, Qt.AlignmentFlag.AlignTop)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        lay.addLayout(head)
        sub = QLabel(subtitle or f"ID: {info.id}")
        sub.setStyleSheet(f"color:{theme.MUTED};")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addSpacing(2)

        def row(ico: str, *widgets) -> None:
            r = QHBoxLayout()
            r.setSpacing(6)
            r.addWidget(_icon_label(ico))
            for w in widgets:
                r.addWidget(QLabel(w) if isinstance(w, str) else w)
            r.addStretch()
            lay.addLayout(r)

        if m is not None:
            row("user", "Penulis: " + (", ".join(m.authors) or "-"))
            row("cpu", f"Model: {Path(m.model.weights).name} "
                       f"[{', '.join(m.model.classes) or 'semua kelas'}]")
        row("pin", "Lokasi:", _pill("bawaan" if _is_bundled(info.path) else "terpasang",
                                    "blue" if _is_bundled(info.path) else "purple"))

        status = QHBoxLayout()
        status.setSpacing(6)
        status.addWidget(_icon_label("check_circle", theme.OK if info.valid else theme.BAD))
        status.addWidget(QLabel("Status:"))
        status.addWidget(_pill("Valid", "teal") if info.valid else _pill("Tidak valid", "red"))
        if info.warnings:
            w = _pill(f"⚠ {len(info.warnings)}", "orange")
            w.setToolTip("\n".join(info.warnings))
            status.addWidget(w)
        status.addStretch()
        cams = QLabel(f"✓ {cameras} kamera" if cameras else "belum dipakai")
        cams.setStyleSheet(f"color:{theme.MUTED};")
        status.addWidget(cams)
        lay.addLayout(status)
        if info.errors:
            self.setToolTip("\n".join(info.errors))

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e) -> None:
        self.clicked.emit(self.info.id)
        super().mousePressEvent(e)


class AddCard(QFrame):
    """Kartu bergaris putus-putus: pasang modul baru."""
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("addCard")
        self.setFixedWidth(CARD_W)
        self.setMinimumHeight(170)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.addStretch()
        plus = _icon_label("plus", theme.MUTED, 30)
        plus.setAlignment(Qt.AlignmentFlag.AlignCenter)
        txt = QLabel("Pasang modul baru")
        txt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        txt.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(plus)
        lay.addWidget(txt)
        lay.addStretch()

    def mousePressEvent(self, e) -> None:
        self.clicked.emit()


class Section(QWidget):
    """Bagian yang bisa dibuka-tutup di panel rincian."""

    def __init__(self, title: str, body: QWidget, expanded: bool = False):
        super().__init__()
        self.body = body
        self.btn = QToolButton()
        self.btn.setObjectName("section")
        self.btn.setText(title)
        self.btn.setCheckable(True)
        self.btn.setChecked(expanded)
        self.btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)   # panah di kanan
        self.btn.setSizePolicy(self.btn.sizePolicy().Policy.Expanding,
                               self.btn.sizePolicy().verticalPolicy())
        self.btn.toggled.connect(self._toggle)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.btn)
        lay.addWidget(body)
        self._toggle(expanded)

    def _toggle(self, on: bool) -> None:
        self.body.setVisible(on)
        self.btn.setIcon(icon("chevron" if on else "chevron_r", theme.MUTED))
        self.btn.setIconSize(QSize(14, 14))


class ModulesPage(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._testing = False
        self.selected = ""
        self.cards: dict[str, ModuleCard] = {}
        self._test_output: dict[str, str] = {}
        self._setup_ui()
        manager.modules_changed.connect(self.refresh)
        manager.cameras_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self) -> None:
        def btn(text, ico, obj="", color=theme.TEXT):
            b = QPushButton(icon(ico, color), f" {text}")
            b.setIconSize(QSize(15, 15))
            if obj:
                b.setObjectName(obj)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            return b

        self.btn_folder = btn("Pasang dari Folder…", "plus")
        self.btn_zip = btn("Pasang dari ZIP…", "archive")
        self.btn_reload = btn("Muat Ulang", "refresh")
        self.btn_test = btn("Uji Modul", "flask", "warn", theme.WARN)
        self.btn_test.setToolTip("Muat bobot & kode modul terpilih lalu jalankan beberapa frame "
                                 "(uji kepatuhan kontrak)")
        self.btn_folder.clicked.connect(lambda: self._install(zip_file=False))
        self.btn_zip.clicked.connect(lambda: self._install(zip_file=True))
        self.btn_reload.clicked.connect(self.manager.reload_modules)
        self.btn_test.clicked.connect(self._test)
        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("search")
        self.ed_search.setPlaceholderText("Cari modul…")
        self.ed_search.addAction(icon("search", theme.MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self._relayout)

        bar = QHBoxLayout()
        for b in (self.btn_folder, self.btn_zip, self.btn_reload, self.btn_test):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(self.ed_search)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.grid_host)
        self.add_card = AddCard()
        self.add_card.clicked.connect(self._install_menu)

        left = QVBoxLayout()
        left.addLayout(bar)
        left.addWidget(self.scroll, 1)

        # panel rincian (kanan)
        self.detail_host = QWidget()
        self.detail = QVBoxLayout(self.detail_host)
        self.detail.setContentsMargins(0, 0, 6, 0)
        self.detail.setSpacing(10)
        dscroll = QScrollArea()
        dscroll.setWidgetResizable(True)
        dscroll.setFrameShape(QFrame.Shape.NoFrame)
        dscroll.setWidget(self.detail_host)
        title = QLabel("Rincian Modul")
        title.setObjectName("pageTitle")
        right = QVBoxLayout()
        right.addWidget(title)
        right.addWidget(dscroll, 1)
        right_w = QWidget()
        right_w.setFixedWidth(340)
        right_w.setLayout(right)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(right_w)

    # ------------------------------------------------------------------ isi
    def refresh(self) -> None:
        for c in self.cards.values():
            c.deleteLater()
        self.cards = {}
        usage: dict[str, int] = {}
        for cam in self.manager.cameras.values():
            mid = self.manager.module_id(cam)
            usage[mid] = usage.get(mid, 0) + 1
        for info in sorted(self.manager.modules.values(), key=lambda i: i.id):
            card = ModuleCard(info, usage.get(info.id, 0))
            card.clicked.connect(self.select)
            self.cards[info.id] = card
        if self.selected not in self.cards:
            self.selected = next(iter(self.cards), "")
        self._relayout()
        self.select(self.selected)

    def _columns(self) -> int:
        return max(1, (self.scroll.viewport().width() + 14) // (CARD_W + 14))

    def _relayout(self, *_args) -> None:
        while self.grid.count():
            self.grid.takeAt(0)
        q = self.ed_search.text().strip().lower()
        shown = []
        for mid, card in self.cards.items():
            m = card.info.manifest
            hay = " ".join([mid, m.name if m else "", ", ".join(m.authors) if m else ""]).lower()
            visible = not q or q in hay
            card.setVisible(visible)
            if visible:
                shown.append(card)
        cols = self._columns()
        items = [*shown, self.add_card]
        for r in range(self.grid.rowCount()):
            self.grid.setRowStretch(r, 0)
        for i, w in enumerate(items):
            self.grid.addWidget(w, i // cols, i % cols, Qt.AlignmentFlag.AlignTop)
        self.grid.setRowStretch((len(items) - 1) // cols + 1, 1)   # sisa tinggi ke baris kosong

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def select(self, mid: str) -> None:
        self.selected = mid
        for k, card in self.cards.items():
            card.set_selected(k == mid)
        self.btn_test.setEnabled(bool(mid) and not self._testing)
        self._show_detail()

    def _selected(self) -> str:
        return self.selected

    # ------------------------------------------------------------- rincian
    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        f = QFrame()
        f.setObjectName("card")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(12, 10, 12, 12)
        h = QLabel(title)
        h.setObjectName("sectionTitle")
        h.setWordWrap(True)
        lay.addWidget(h)
        return f, lay

    def _path_row(self, path: str, action: str) -> QWidget:
        w = QFrame()
        w.setObjectName("addr")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 2, 0)
        ed = QLineEdit(path)
        ed.setReadOnly(True)
        ed.setCursorPosition(0)
        ed.setToolTip(path)
        b = QToolButton()
        b.setObjectName("rowBtn")
        if action == "open":
            b.setIcon(icon("folder", theme.TEXT))
            b.setToolTip("Buka folder")
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        else:
            b.setIcon(icon("copy", theme.TEXT))
            b.setToolTip("Salin")
            b.clicked.connect(lambda: (QApplication.clipboard().setText(path),
                                       QToolTip.showText(b.mapToGlobal(b.rect().bottomLeft()),
                                                         "Disalin", b)))
        lay.addWidget(ed, 1)
        lay.addWidget(b)
        return w

    def _thumb(self, cam) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        lbl = QLabel()
        lbl.setObjectName("thumb")
        lbl.setFixedSize(92, 60)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame = self.manager.frames.get(cam.id)
        if frame is not None:                       # kamera berjalan: frame terakhir (blur ikut)
            img = np.ascontiguousarray(frame.annotated)
            lbl.setPixmap(QPixmap.fromImage(to_qimage(img)).scaled(
                92, 60, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))
        else:
            lbl.setPixmap(icon("camera", theme.MUTED).pixmap(24, 24))
            lbl.setToolTip("Kamera tidak sedang berjalan")
        name = QLabel(cam.name)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setMaximumWidth(92)
        lay.addWidget(lbl)
        lay.addWidget(name)
        return box

    def _show_detail(self) -> None:
        while self.detail.count():
            item = self.detail.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        info = self.manager.modules.get(self.selected)
        if info is None:
            self.detail.addWidget(QLabel("Belum ada modul terpasang."))
            self.detail.addStretch()
            return
        m = info.manifest

        card, lay = self._card(f"Detail Modul: {m.name if m else info.path.name} ({info.id})")
        if m is not None:
            meta = QLabel(f"Versi {m.version} · kontrak api {m.api_version}<br>"
                          f"Penulis: {', '.join(m.authors) or '-'} · Lisensi: {m.license or '-'}")
            meta.setWordWrap(True)
            meta.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(meta)
        if info.errors:
            err = QLabel("Masalah:\n" + "\n".join(f"• {e}" for e in info.errors))
            err.setWordWrap(True)
            err.setStyleSheet(f"color:{theme.BAD};")
            lay.addWidget(err)
        self.detail.addWidget(card)

        card, lay = self._card("Lokasi & Kontrak")
        lay.addWidget(QLabel("Folder modul ini:"))
        lay.addWidget(self._path_row(str(info.path), "open"))
        lay.addWidget(QLabel("Modul bawaan:"))
        lay.addWidget(self._path_row(str(BUNDLED_MODULES_DIR), "copy"))
        lay.addWidget(QLabel("Modul terpasang:"))
        lay.addWidget(self._path_row(str(USER_MODULES_DIR), "open"))
        lay.addWidget(QLabel("Kontrak:"))
        lay.addWidget(self._path_row(CONTRACT, "copy"))
        self.detail.addWidget(card)

        card, lay = self._card("Kamera yang Terhubung")
        cams = [c for c in sorted(self.manager.cameras.values(), key=lambda c: c.name.lower())
                if self.manager.module_id(c) == info.id]
        if not cams:
            none = QLabel("Belum ada kamera yang memakai modul ini.")
            none.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(none)
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, cam in enumerate(cams):
            grid.addWidget(self._thumb(cam), i // 3, i % 3, Qt.AlignmentFlag.AlignLeft)
        lay.addLayout(grid)
        self.detail.addWidget(card)

        if m is not None:
            card, lay = self._card("Rincian teknis")
            presets, _ = m.load_presets()
            parts = [
                ("Model", f"{m.model.weights}\ntask {m.model.task} · imgsz {m.model.imgsz} · "
                          f"conf {m.model.conf} · iou {m.model.iou}"),
                ("Peran zona/garis", "\n".join(f"• {r.role} ({r.kind}) {r.label}"
                                               for r in m.regions) or "(tidak ada)"),
                ("Parameter (diatur per kamera)", "\n".join(
                    f"• {k}: {p.type} = {p.default}  {p.label}" for k, p in m.params.items())
                    or "(tidak ada)"),
                ("Metrik", "\n".join(f"• {x.key}: {x.label} [{x.unit or '-'}]"
                                     f"{' per zona' if x.per_region else ''}, agg {x.agg}"
                                     for x in m.metrics) or "(tidak ada)"),
                ("Event", "\n".join(f"• {e.type}: {e.label}" for e in m.events) or "(tidak ada)"),
                ("Preset", "\n".join(f"• {p.label}" for p in presets) or "(tidak ada)"),
            ]
            if info.warnings:
                parts.append(("Peringatan", "\n".join(f"• {w}" for w in info.warnings)))
            for i, (title, text) in enumerate(parts):
                body = QLabel(text)
                body.setWordWrap(True)
                body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                body.setStyleSheet(f"color:{theme.MUTED};")
                lay.addWidget(Section(title, body, expanded=i == 0))
            self.detail.addWidget(card)

        card, lay = self._card("Hasil Uji Modul")
        self.out = QPlainTextEdit(self._test_output.get(info.id, "Belum diuji. Klik “Uji Modul”."))
        self.out.setObjectName("mono")
        self.out.setReadOnly(True)
        self.out.setMinimumHeight(140)
        lay.addWidget(self.out)
        hint = QLabel("Uji penuh dengan video & paritas: python scripts/check_module.py "
                      "<folder> --video … --expected …")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme.MUTED}; font-size:11px;")
        lay.addWidget(hint)
        self.detail.addWidget(card)
        self.detail.addStretch()

    # --------------------------------------------------------------- aksi
    def _install_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(icon("folder", theme.TEXT), "Dari folder…", lambda: self._install(False))
        menu.addAction(icon("archive", theme.TEXT), "Dari ZIP…", lambda: self._install(True))
        menu.exec(self.add_card.mapToGlobal(self.add_card.rect().center()))

    def _install(self, zip_file: bool) -> None:
        if zip_file:
            path, _ = QFileDialog.getOpenFileName(self, "Pilih paket modul", "", "ZIP (*.zip)")
        else:
            path = QFileDialog.getExistingDirectory(self, "Pilih folder modul (berisi module.yaml)")
        if not path:
            return
        digest = f"\n\nSHA-256: {_sha256(Path(path))}" if zip_file else ""
        if QMessageBox.question(self, "Pasang modul", f"{TRUST_NOTE}\n\nPasang dari:\n{path}"
                                f"{digest}") != QMessageBox.StandardButton.Yes:
            return
        try:
            info = registry.install(path)
        except FileExistsError as e:
            if QMessageBox.question(self, "Modul sudah ada", f"{e}. Ganti dengan versi ini?\n"
                                    "Kamera yang sedang berjalan tetap memakai versi lama sampai "
                                    "dijalankan ulang.") != QMessageBox.StandardButton.Yes:
                return
            info = registry.install(path, replace=True)
        except Exception as e:                     # manifest rusak, ZIP tidak aman, dsb.
            QMessageBox.warning(self, "Gagal memasang modul", str(e))
            return
        self.selected = info.id
        self.manager.reload_modules()
        status = "valid" if info.valid else "TIDAK VALID:\n- " + "\n- ".join(info.errors)
        QMessageBox.information(self, "Modul terpasang", f"{info.id} terpasang di\n{info.path}\n\n"
                                f"Status: {status}")

    def _test(self) -> None:
        info = self.manager.modules.get(self._selected())
        if info is None:
            return
        self._testing = True
        self.btn_test.setEnabled(False)
        self._test_output[info.id] = f"Menguji {info.id}… (memuat model & kode modul)"
        self.out.setPlainText(self._test_output[info.id])
        mid = info.id
        run_async(_quick_check, str(info.path), on_done=lambda r: self._on_tested(mid, r),
                  on_failed=lambda msg: self._on_tested(mid, (False, msg)))

    def _on_tested(self, mid: str, result) -> None:
        ok, text = result
        self._testing = False
        self._test_output[mid] = ("LULUS" if ok else "GAGAL") + "\n\n" + text
        self.btn_test.setEnabled(True)
        if mid == self.selected:
            self.out.setPlainText(self._test_output[mid])
