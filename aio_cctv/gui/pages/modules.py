"""Halaman Modul: kartu modul terpasang + panel rincian, pasang & uji paket modul (Fase 6 §5.6)."""
import hashlib
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QSize, Qt, QTimer, QUrl, pyqtSignal
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
CARD_W = 270                                     # lebar kartu; jumlah kolom menyesuaikan layar
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


def _module_icon(info) -> str:
    """Ikon kartu: orang untuk modul pengunjung/pegawai, selain itu ikon modul."""
    m = info.manifest
    text = " ".join([info.id, m.name if m else ""]).lower()
    return "user" if any(k in text for k in ("pengunjung", "visitor", "pegawai", "person")) \
        else "module"


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
        if subtitle:
            self.setToolTip(f"{m.name if m else title}\nID: {info.id}")

        head = QHBoxLayout()
        head.setSpacing(10)
        ico = _icon_label(_module_icon(info), "#4FA3F7", 40)
        head.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
        # judul panjang dipecah manual jadi 2 baris: QLabel wordWrap di dalam HBox sering
        # salah menghitung tinggi sehingga baris kedua terpotong
        if len(title) > 11 and " " in title:
            title = title.replace(" ", "\n", 1)
        t = QLabel(title)
        t.setObjectName("cardTitle")
        t.setStyleSheet("font-size:16px; font-weight:bold;")
        head.addWidget(t, 1, Qt.AlignmentFlag.AlignTop)
        ver = _pill(f"Versi {m.version}", "gray") if m is not None else _pill("Rusak", "red")
        head.addWidget(ver, 0, Qt.AlignmentFlag.AlignTop)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(7)
        lay.addLayout(head)
        lay.addSpacing(4)

        def row(ico_name: str, color: str, *widgets) -> None:
            r = QHBoxLayout()
            r.setSpacing(6)
            r.addWidget(_icon_label(ico_name, color))
            for w in widgets:
                r.addWidget(QLabel(w) if isinstance(w, str) else w)
            r.addStretch()
            lay.addLayout(r)

        if m is not None:
            row("user", theme.MUTED, "Penulis: " + (", ".join(m.authors) or "-"))
        if not info.valid:
            st, ico_s, col = _pill("Tidak valid", "red"), "x_circle", theme.BAD
            st.setToolTip("\n".join(info.errors))
        elif cameras:
            st, ico_s, col = _pill(f"Aktif ({cameras} Kamera)", "green"), "check_circle", theme.OK
        else:
            st, ico_s, col = _pill("Belum Dipakai", "gray"), "clock", theme.MUTED
        row(ico_s, col, "Status:", st)
        if info.warnings:
            w = QLabel(f"⚠ {len(info.warnings)} peringatan")
            w.setStyleSheet(f"color:{theme.WARN};")
            w.setToolTip("\n".join(info.warnings))
            lay.addWidget(w)
        lay.addStretch()

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
        self.setMinimumHeight(120)
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
        manager.worker_started.connect(lambda _: self._build_overview())
        manager.worker_stopped.connect(lambda _: self._build_overview())
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

        self.btn_install = btn("Pasang Modul Baru", "plus", "primary", "#FFFFFF")
        self.btn_install.setToolTip("Pasang paket modul dari folder atau ZIP")
        self.btn_install.clicked.connect(lambda: self._install_menu(self.btn_install))
        self.btn_reload = btn("Muat Ulang", "refresh")
        self.btn_reload.setToolTip("Pindai ulang folder modul")
        self.btn_reload.clicked.connect(self.manager.reload_modules)
        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("search")
        self.ed_search.setPlaceholderText("Cari modul…")
        self.ed_search.addAction(icon("search", theme.MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self._relayout)

        bar = QHBoxLayout()
        bar.addWidget(self.btn_install)
        bar.addWidget(self.btn_reload)
        bar.addWidget(self.ed_search, 1)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.overview = QVBoxLayout()              # ringkasan & pemakaian modul
        self.overview.setSpacing(12)
        left_host = QWidget()
        lh = QVBoxLayout(left_host)
        lh.setContentsMargins(0, 0, 6, 0)
        lh.setSpacing(18)
        lh.addWidget(self.grid_host)
        lh.addLayout(self.overview)
        lh.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(left_host)
        self.add_card = AddCard()
        self.add_card.clicked.connect(lambda: self._install_menu(self.add_card))

        left = QVBoxLayout()
        left.addLayout(bar)
        left.addWidget(self.scroll, 1)

        # panel rincian (kanan): judul + dua kolom
        self.btn_test = btn("Uji Modul", "play", "primary", "#FFFFFF")
        self.btn_test.setToolTip("Muat bobot & kode modul terpilih lalu jalankan beberapa frame "
                                 "(uji kepatuhan kontrak)")
        self.btn_test.clicked.connect(self._test)
        self.btn_remove = btn("Hapus Modul", "trash", "danger", theme.BAD)
        self.btn_remove.clicked.connect(self._uninstall)
        self.lbl_detail_title = QLabel("Rincian Modul")
        self.lbl_detail_title.setObjectName("pageTitle")
        self.lbl_detail_title.setWordWrap(True)

        self.col_a = QVBoxLayout()                 # informasi, lokasi, kamera, teknis
        self.col_a.setSpacing(10)
        self.col_b = QVBoxLayout()                 # aksi, folder, hasil uji
        self.col_b.setSpacing(10)
        actions = QHBoxLayout()
        actions.addWidget(self.btn_test)
        actions.addWidget(self.btn_remove)
        self.col_b_dyn = QVBoxLayout()
        self.col_b_dyn.setSpacing(10)
        self.col_b.addLayout(actions)
        self.col_b.addLayout(self.col_b_dyn, 1)
        cols = QHBoxLayout()
        cols.setSpacing(10)
        cols.addLayout(self.col_a, 1)
        cols.addLayout(self.col_b, 1)
        self.detail_host = QWidget()
        dl = QVBoxLayout(self.detail_host)
        dl.setContentsMargins(0, 0, 6, 0)
        dl.addLayout(cols, 1)                      # kolom mengisi tinggi panel
        dscroll = QScrollArea()
        dscroll.setWidgetResizable(True)
        dscroll.setFrameShape(QFrame.Shape.NoFrame)
        dscroll.setWidget(self.detail_host)
        right = QVBoxLayout()
        right.addWidget(self.lbl_detail_title)
        right.addWidget(dscroll, 1)
        right_w = QFrame(objectName="detailPanel")
        right_w.setStyleSheet(f"QFrame#detailPanel {{ border-left:1px solid {theme.BORDER}; }}")
        self.right_w = right_w                     # lebar diatur di resizeEvent (ikut jendela)
        right_w.setFixedWidth(600)
        dscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        self._build_overview()

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


    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        # panel rincian ±38% lebar halaman (600–900 px): di layar lebar tidak sempit/terpotong,
        # di layar kecil kartu modul tetap kebagian ruang
        self.right_w.setFixedWidth(max(600, min(900, int(self.width() * 0.38))))
        # hitung kolom grid SETELAH lebar panel diterapkan (viewport baru berubah di event berikut)
        QTimer.singleShot(0, self._relayout)

    def select(self, mid: str) -> None:
        self.selected = mid
        for k, card in self.cards.items():
            card.set_selected(k == mid)
        self.btn_test.setEnabled(bool(mid) and not self._testing)
        info = self.manager.modules.get(mid)
        removable = info is not None and registry.is_removable(info)
        self.btn_remove.setEnabled(removable)
        self.btn_remove.setToolTip("Hapus modul ini dari komputer" if removable else
                                   "Modul bawaan aplikasi tidak bisa dihapus")
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

    def _path_row(self, path: str, action: str, elide_left: bool = False) -> QWidget:
        w = QFrame()
        w.setObjectName("addr")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 2, 0)
        ed = QLineEdit(path)
        ed.setMinimumWidth(60)                     # path panjang tidak melebarkan panel
        ed.setReadOnly(True)
        ed.setCursorPosition(len(path) if elide_left else 0)   # bagian akhir path paling penting
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

    @staticmethod
    def _clear(layout) -> None:
        """Kosongkan layout termasuk layout bersarang (baris tile, tabel)."""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout() is not None:
                ModulesPage._clear(item.layout())
                item.layout().deleteLater()

    def _info_row(self, lay, ico_name: str, text: str) -> None:
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(_icon_label(ico_name))
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        r.addWidget(lbl, 1)
        lay.addLayout(r)

    def _show_detail(self) -> None:
        self._clear(self.col_a)
        self._clear(self.col_b_dyn)
        info = self.manager.modules.get(self.selected)
        if info is None:
            self.lbl_detail_title.setText("Rincian Modul")
            self.col_a.addWidget(QLabel("Belum ada modul terpasang."))
            return
        m = info.manifest
        title, _ = _split_name(m.name if m else info.path.name)
        self.lbl_detail_title.setText(f"Rincian Modul: {title}")

        # --- kolom A
        card, lay = self._card("Informasi & Kontrak")
        if m is not None:
            self._info_row(lay, "report", f"Nama: {m.name}")
            self._info_row(lay, "history", f"Versi: {m.version}")
            self._info_row(lay, "shield", f"Kontrak API: {m.api_version}")
            self._info_row(lay, "user", f"Penulis: {', '.join(m.authors) or '-'}")
            self._info_row(lay, "check_circle", f"Lisensi: {m.license or '-'}")
        self._info_row(lay, "key", f"ID: {info.id}")
        if info.errors:
            err = QLabel("Masalah:\n" + "\n".join(f"• {e}" for e in info.errors))
            err.setWordWrap(True)
            err.setStyleSheet(f"color:{theme.BAD};")
            lay.addWidget(err)
        self.col_a.addWidget(card)

        card, lay = self._card("Lokasi File")
        where = QLabel("Modul bawaan aplikasi" if _is_bundled(info.path) else
                       "Dipasang pengguna")
        where.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(where)
        lay.addWidget(self._path_row(str(info.path), "copy", elide_left=True))
        self.col_a.addWidget(card)

        card, lay = self._card("Kamera yang Terhubung")
        cams = [c for c in sorted(self.manager.cameras.values(), key=lambda c: c.name.lower())
                if self.manager.module_id(c) == info.id]
        if not cams:
            none = QLabel("Tidak ada kamera yang terhubung")
            none.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(none)
        for cam in cams:
            r = QHBoxLayout()
            r.setSpacing(8)
            r.addWidget(_icon_label("camera", theme.MUTED))
            name = QLabel(cam.name)
            name.setToolTip(cam.name)
            r.addWidget(name, 1)
            running = self.manager.is_running(cam.id)
            r.addWidget(_pill("Aktif", "green") if running else _pill("Berhenti", "gray"))
            lay.addLayout(r)
        self.col_a.addWidget(card)

        if m is not None:
            card, lay = self._card("Parameter & Teknis")
            presets, _ = m.load_presets()
            parts = [
                (f"Model ({Path(m.model.weights).name})",
                 (f"{m.model.weights}\ntask {m.model.task} · imgsz {m.model.imgsz} · "
                  f"conf {m.model.conf} · iou {m.model.iou}\nkelas: "
                  f"{', '.join(m.model.classes) or 'semua'}")),
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
            for i, (title_s, text) in enumerate(parts):
                body = QLabel(text)
                body.setWordWrap(True)
                body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                body.setStyleSheet(f"color:{theme.MUTED};")
                lay.addWidget(Section(title_s, body, expanded=i == 0))
            lay.addStretch()
            self.col_a.addWidget(card, 1)              # kartu terakhir memanjang ke bawah

        # --- kolom B
        card, lay = self._card("Folder")
        lay.addWidget(QLabel("Folder modul ini:"))
        lay.addWidget(self._path_row(str(info.path), "open", elide_left=True))
        lay.addWidget(QLabel("Modul terpasang:"))
        lay.addWidget(self._path_row(str(USER_MODULES_DIR), "open", elide_left=True))
        lay.addWidget(QLabel("Kontrak modul:"))
        lay.addWidget(self._path_row(CONTRACT, "copy"))
        self.col_b_dyn.addWidget(card)

        card, lay = self._card("Hasil Uji Modul")
        self.out = QPlainTextEdit(self._test_output.get(
            info.id, "Belum diuji. Klik 'Uji Modul' di atas untuk memulai."))
        self.out.setObjectName("mono")
        self.out.setReadOnly(True)
        self.out.setMinimumHeight(260)
        lay.addWidget(self.out)
        hint = QLabel("Uji penuh dengan video & paritas: python scripts/check_module.py "
                      "<folder> --video … --expected …")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme.MUTED}; font-size:11px;")
        lay.addWidget(hint)
        self.col_b_dyn.addWidget(card, 1)              # hasil uji memanjang ke bawah

    # ------------------------------------------------------------ ringkasan
    def _build_overview(self) -> None:
        """Bagian bawah area kartu: ringkasan angka & pemakaian modul per kamera."""
        self._clear(self.overview)
        mods = list(self.manager.modules.values())
        cams = sorted(self.manager.cameras.values(), key=lambda c: c.name.lower())
        used = [c for c in cams if self.manager.module_id(c) in self.manager.modules]
        running = [c for c in cams if self.manager.is_running(c.id)]

        title = QLabel("Ringkasan")
        title.setObjectName("sectionTitle")
        self.overview.addWidget(title)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        for ico_name, label, value in (
                ("module", "Modul terpasang", f"{len(mods)}"),
                ("check_circle", "Modul valid", f"{sum(m.valid for m in mods)} / {len(mods)}"),
                ("camera", "Kamera memakai modul", f"{len(used)} / {len(cams)}"),
                ("play", "Kamera berjalan", f"{len(running)}")):
            tiles.addWidget(self._tile(ico_name, label, value), 1)
        self.overview.addLayout(tiles)

        row = QHBoxLayout()
        row.setSpacing(12)
        card, lay = self._card("Pemakaian Modul per Kamera")
        if not cams:
            none = QLabel("Belum ada kamera. Tambahkan di halaman Kamera.")
            none.setStyleSheet(f"color:{theme.MUTED};")
            lay.addWidget(none)
        else:
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(8)
            for col, h in enumerate(("Kamera", "Modul", "Zona / Garis", "Status")):
                hl = QLabel(h)
                hl.setStyleSheet(f"color:{theme.MUTED}; font-weight:bold;")
                grid.addWidget(hl, 0, col)
            for r, cam in enumerate(cams, 1):
                mid = self.manager.module_id(cam)
                info = self.manager.modules.get(mid)
                prof = self.manager.profile(cam)
                name = QLabel(cam.name)
                name.setToolTip(cam.name)
                grid.addWidget(name, r, 0)
                mod = _pill(mid, "teal" if info is not None and info.valid else "red")
                mod.setCursor(Qt.CursorShape.PointingHandCursor)
                mod.mousePressEvent = lambda _e, m=mid: m in self.cards and self.select(m)
                grid.addWidget(mod, r, 1, Qt.AlignmentFlag.AlignLeft)
                grid.addWidget(QLabel(f"{len(prof.zones)} • {len(prof.lines)}"), r, 2)
                on = self.manager.is_running(cam.id)
                grid.addWidget(_pill("Berjalan", "green") if on else _pill("Berhenti", "gray"),
                               r, 3, Qt.AlignmentFlag.AlignLeft)
            grid.setColumnStretch(0, 2)
            grid.setColumnStretch(1, 2)
            lay.addLayout(grid)
        lay.addStretch()
        row.addWidget(card, 3)

        self.overview.addLayout(row)

    @staticmethod
    def _tile(ico_name: str, label: str, value: str) -> QFrame:
        f = QFrame(objectName="tile")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        lay.addWidget(_icon_label(ico_name, "#4FA3F7", 26))
        col = QVBoxLayout()
        col.setSpacing(0)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{theme.MUTED};")
        v = QLabel(value)
        v.setObjectName("tileValue")
        col.addWidget(lbl)
        col.addWidget(v)
        lay.addLayout(col, 1)
        return f

    # --------------------------------------------------------------- aksi
    def _install_menu(self, anchor: QWidget) -> None:
        menu = QMenu(self)
        menu.addAction(icon("folder", theme.TEXT), "Dari folder…", lambda: self._install(False))
        menu.addAction(icon("archive", theme.TEXT), "Dari ZIP…", lambda: self._install(True))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _uninstall(self) -> None:
        info = self.manager.modules.get(self._selected())
        if info is None or not registry.is_removable(info):
            return
        users = [c.name for c in self.manager.cameras.values()
                 if self.manager.module_id(c) == info.id]
        warn = (f"\n\n⚠ Dipakai {len(users)} kamera ({', '.join(users)}). Kamera itu akan gagal "
                "dijalankan sampai modulnya diganti di dialog kamera.") if users else ""
        if QMessageBox.question(self, "Hapus modul", f"Hapus modul “{info.id}” dari komputer ini?"
                                f"\n{info.path}{warn}") != QMessageBox.StandardButton.Yes:
            return
        for c in self.manager.cameras.values():    # hentikan kamera yang memakai modul ini
            if self.manager.module_id(c) == info.id:
                self.manager.stop(c.id)
        try:
            registry.uninstall(info)
        except OSError as e:
            QMessageBox.warning(self, "Gagal menghapus modul", str(e))
            return
        self.selected = ""
        self.manager.reload_modules()

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
