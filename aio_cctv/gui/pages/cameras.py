"""Daftar kamera dalam bentuk kartu + tambah/ubah/hapus (Fase 5 §3, §5.5; tampilan baru Fase 8).

Setiap kartu: nama & lokasi, jenis, alamat (bisa disalin, password tersamar), stream, modul,
zona/garis, status, sakelar aktif, menu ⋮, serta tombol "Lihat Live" & "Pengaturan".
"""
import logging

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core import secrets
from aio_cctv.core.profile_io import apply_import, export_profile, import_profile
from aio_cctv.gui import theme
from aio_cctv.gui.camera_manager import CameraConfig, CameraManager, source_config
from aio_cctv.gui.dialogs.add_camera import AddCameraDialog
from aio_cctv.gui.dialogs.preset_wizard import PresetWizard
from aio_cctv.gui.icons import icon
from aio_cctv.sources.url_builder import BRANDS

log = logging.getLogger(__name__)

# (judul, lebar tetap px atau None = melebar, rata tengah?) — dipakai kepala tabel & tiap kartu
COLUMNS = [("Nama", None, False), ("Jenis", 105, True), ("Alamat", None, False),
           ("Stream", 60, True), ("Modul", 105, True), ("Zona/Garis", 135, True),
           ("Status", 100, False), ("Aktif", 50, True), ("", 28, True), ("Aksi", 136, True)]
STRETCH = {"Nama": 2, "Alamat": 4}
MARGIN_H = 14                                     # padding kiri/kanan kartu = kepala tabel


class ElidedLabel(QLabel):
    """Label yang memotong teks panjang dengan "…" sesuai lebar yang tersedia."""

    def __init__(self, text: str):
        super().__init__(text)
        self._full = text
        self.setToolTip(text)
        # lebar diabaikan: nama panjang tidak boleh menggeser kolom lain antar-kartu
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight,
                                                   self.width()))


def _pill(text: str, kind: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("pill", kind)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


def _btn(text: str, ico: str, obj: str = "", tip: str = "") -> QPushButton:
    b = QPushButton(icon(ico, "#FFFFFF" if obj == "primary" else theme.BAD if obj == "danger"
                         else theme.TEXT), f" {text}" if text else "")
    b.setIconSize(QSize(15, 15))
    if obj:
        b.setObjectName(obj)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _row(widgets: list[QWidget]) -> QHBoxLayout:
    """Susun satu baris kolom dengan lebar yang sama untuk kepala tabel & kartu."""
    row = QHBoxLayout()
    row.setContentsMargins(MARGIN_H, 0, MARGIN_H, 0)
    row.setSpacing(10)
    for (title, width, center), w in zip(COLUMNS, widgets):
        box = QWidget()                            # tanpa stylesheet: aturan tanpa selektor
        lay = QHBoxLayout(box)                     # akan turun ke anak & menghapus latar pill
        lay.setContentsMargins(0, 0, 0, 0)
        if center:
            lay.addStretch()
        lay.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)   # pill tetap kecil, tidak setinggi baris
        if center or isinstance(w, QLabel) and not isinstance(w, QLineEdit):
            lay.addStretch()
        if width:
            box.setFixedWidth(width)
        row.addWidget(box, STRETCH.get(title, 0))
    return row


class CameraCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, page: "CamerasPage", cam: CameraConfig):
        super().__init__()
        self.page, self.cam = page, cam
        self.setObjectName("card")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        mgr = page.manager
        running = mgr.is_running(cam.id)

        # Nama + lokasi
        name = QWidget()
        nl = QVBoxLayout(name)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(4)
        title = ElidedLabel(cam.name)
        title.setObjectName("cardTitle")
        nl.addWidget(title)
        if cam.location:
            loc = _pill(cam.location, "gray")
            loc.setSizePolicy(loc.sizePolicy().horizontalPolicy(), loc.sizePolicy().verticalPolicy())
            row = QHBoxLayout()
            row.addWidget(loc)
            row.addStretch()
            nl.addLayout(row)

        # Jenis
        if cam.brand == "file":
            kind = _pill("File video", "orange")
        elif cam.brand == "webcam":
            kind = _pill("Webcam", "purple")
        elif cam.brand == "custom":
            kind = _pill("URL manual", "blue")
        else:
            kind = _pill(BRANDS.get(cam.brand, {}).get("label", cam.brand), "blue")

        # Alamat (sudah tersamar, tidak menyentuh keyring) + Salin
        addr = QFrame()
        addr.setObjectName("addr")
        al = QHBoxLayout(addr)
        al.setContentsMargins(0, 0, 3, 0)
        al.setSpacing(2)
        uri = source_config(cam)["uri"]
        ed = QLineEdit(uri)
        ed.setMinimumWidth(60)
        ed.setReadOnly(True)
        ed.setCursorPosition(0)
        ed.setToolTip(uri)
        btn_copy = _btn("Salin", "copy", "small", "Salin alamat (password tetap tersamar)")
        btn_copy.clicked.connect(lambda: self._copy(uri, btn_copy))
        al.addWidget(ed, 1)
        al.addWidget(btn_copy)

        stream = QLabel("—" if cam.brand in ("file", "webcam") else cam.stream)

        # Modul
        mid = mgr.module_id(cam)
        info = mgr.modules.get(mid)
        if info is None:
            mod = _pill(f"{mid} ✕", "red")
            mod.setToolTip("Modul tidak terpasang")
        elif not info.valid:
            mod = _pill(f"{mid} ⚠", "red")
            mod.setToolTip("Modul tidak valid: " + "; ".join(info.errors))
        else:
            mod = _pill(mid, "teal")
            mod.setToolTip(f"{info.manifest.name} {info.manifest.version}")

        # Zona/garis (klik = buka editor zona)
        prof = mgr.profile(cam)
        zones = QPushButton(icon("list", theme.PILLS["gray"][0]),
                            f" {len(prof.zones)} zona / {len(prof.lines)} garis")
        zones.setIconSize(QSize(13, 13))
        zones.setProperty("pill", "gray")
        zones.setCursor(Qt.CursorShape.PointingHandCursor)
        zones.setToolTip("Gambar / ubah zona")
        zones.clicked.connect(lambda: page.zones_requested.emit(cam.id))

        # Status
        err = mgr.web_state.camera(cam.id).error if mgr.web_state.camera(cam.id) else ""
        if running and not err:
            color, text = theme.OK, "Berjalan"
        elif err:
            color, text = theme.BAD, "Error"
        elif not cam.enabled:
            color, text = theme.DISABLED, "Nonaktif"
        else:
            color, text = theme.MUTED, "Berhenti"
        status = QLabel(f"<span style='color:{color}; font-size:15px'>●</span> "
                        f"<span style='color:{color}'>{text}</span>")
        if err:
            status.setToolTip(err)

        # Aktif
        sw = QCheckBox()
        sw.setObjectName("switch")
        sw.setChecked(cam.enabled)
        sw.setCursor(Qt.CursorShape.PointingHandCursor)
        sw.setToolTip("Kamera aktif ikut dijalankan oleh \"Mulai semua\"")
        sw.toggled.connect(lambda on: page.set_enabled(cam.id, on))

        # Menu ⋮
        kebab = QToolButton()
        kebab.setObjectName("kebab")
        kebab.setIcon(icon("more", theme.TEXT))
        kebab.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(kebab)
        menu.addAction(icon("stop" if running else "play", theme.TEXT),
                       "Hentikan" if running else "Jalankan", lambda: page.toggle_run(cam.id))
        menu.addAction(icon("edit", theme.TEXT), "Ubah…", lambda: page.edit_camera(cam.id))
        menu.addAction(icon("brush", theme.TEXT), "Gambar zona",
                       lambda: page.zones_requested.emit(cam.id))
        menu.addAction(icon("wand", theme.TEXT), "Wizard preset…", lambda: page.wizard(cam.id))
        menu.addSeparator()
        menu.addAction(icon("export", theme.TEXT), "Ekspor profil…",
                       lambda: page.export_profile(cam.id))
        menu.addAction(icon("import", theme.TEXT), "Impor profil…",
                       lambda: page.import_profile(cam.id))
        menu.addSeparator()
        menu.addAction(icon("trash", theme.BAD), "Hapus kamera…", lambda: page.delete_camera(cam.id))
        kebab.setMenu(menu)

        # Aksi
        actions = QWidget()
        acl = QVBoxLayout(actions)
        acl.setContentsMargins(0, 0, 0, 0)
        acl.setSpacing(5)
        btn_live = _btn("Lihat Live", "eye")
        btn_live.clicked.connect(lambda: page.live_requested.emit(cam.id))
        btn_set = _btn("Pengaturan", "settings")
        btn_set.clicked.connect(lambda: page.edit_camera(cam.id))
        for b in (btn_live, btn_set):
            b.setFixedWidth(130)
            acl.addWidget(b)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.addLayout(_row([name, kind, addr, stream, mod, zones, status, sw, kebab, actions]))

    def _copy(self, text: str, btn: QPushButton) -> None:
        QApplication.clipboard().setText(text)
        QToolTip.showText(btn.mapToGlobal(btn.rect().bottomLeft()), "Disalin", btn)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e) -> None:
        self.clicked.emit(self.cam.id)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        self.page.edit_camera(self.cam.id)


class CamerasPage(QWidget):
    zones_requested = pyqtSignal(str)              # cam_id: buka halaman Zona
    live_requested = pyqtSignal(str)               # cam_id: buka halaman Live

    def __init__(self, manager: CameraManager):
        super().__init__()
        self.manager = manager
        self.selected = ""
        self.cards: dict[str, CameraCard] = {}
        self._setup_ui()
        manager.cameras_changed.connect(self.refresh)
        manager.modules_changed.connect(self.refresh)
        manager.worker_started.connect(lambda _: self.refresh())
        manager.worker_stopped.connect(lambda _: self.refresh())
        self.refresh()

    def _setup_ui(self) -> None:
        self.btn_add = _btn("Tambah Kamera", "plus", "primary")
        self.btn_edit = _btn("Ubah", "edit")
        self.btn_del = _btn("Hapus", "trash", "danger")
        self.btn_zones = _btn("Gambar zona", "brush")
        self.btn_wizard = _btn("Wizard preset…", "wand", tip="Pasang modul + preset: gambar area "
                               "per langkah, aturan & dashboard terpasang otomatis")
        self.btn_export = _btn("Ekspor profil…", "export")
        self.btn_import = _btn("Impor profil…", "import")
        self.btn_add.clicked.connect(lambda: self.add_camera())
        self.btn_edit.clicked.connect(lambda: self.edit_camera())
        self.btn_del.clicked.connect(lambda: self.delete_camera())
        self.btn_zones.clicked.connect(lambda: self._request_zones())
        self.btn_wizard.clicked.connect(lambda: self.wizard())
        self.btn_export.clicked.connect(lambda: self.export_profile())
        self.btn_import.clicked.connect(lambda: self.import_profile())

        bar = QHBoxLayout()
        for b in (self.btn_add, self.btn_edit, self.btn_del, self.btn_zones, self.btn_wizard):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(self.btn_export)
        bar.addWidget(self.btn_import)

        title = QLabel("Kamera")
        title.setObjectName("pageTitle")
        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("search")
        self.ed_search.setPlaceholderText("Cari kamera…")
        self.ed_search.addAction(icon("search", theme.MUTED), QLineEdit.ActionPosition.TrailingPosition)
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self._apply_filter)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self.ed_search)

        headers = []
        for t, _, _ in COLUMNS:
            lbl = QLabel(t)
            lbl.setObjectName("colHeader")
            headers.append(lbl)

        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 4, 0)
        self.list_lay.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.list_host)
        self.empty = QLabel("Belum ada kamera. Klik “+ Tambah Kamera” untuk menambahkan.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color:{theme.MUTED}; padding:30px;")

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addSpacing(6)
        lay.addLayout(head)
        lay.addSpacing(4)
        lay.addLayout(_row(headers))
        lay.addWidget(self.empty)
        lay.addWidget(scroll, 1)

    # ------------------------------------------------------------------ isi
    def refresh(self) -> None:
        # dijadwalkan: sinyal sakelar di kartu bisa memicu refresh saat kartu itu sedang aktif
        QTimer.singleShot(0, self._rebuild)

    def _rebuild(self) -> None:
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards = {}
        cams = sorted(self.manager.cameras.values(), key=lambda c: c.name.lower())
        for cam in cams:
            card = CameraCard(self, cam)
            card.clicked.connect(self.select)
            self.cards[cam.id] = card
            self.list_lay.addWidget(card)
        self.list_lay.addStretch()
        if self.selected not in self.cards:
            self.selected = cams[0].id if cams else ""
        self.select(self.selected)
        self.empty.setVisible(not cams)
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self.ed_search.text().strip().lower()
        for cid, card in self.cards.items():
            cam = card.cam
            hay = " ".join([cam.name, cam.location, source_config(cam)["uri"],
                            self.manager.module_id(cam), cam.brand]).lower()
            card.setVisible(not q or q in hay)

    def select(self, cam_id: str) -> None:
        self.selected = cam_id
        for cid, card in self.cards.items():
            card.set_selected(cid == cam_id)
        has = bool(cam_id)
        for b in (self.btn_edit, self.btn_del, self.btn_zones, self.btn_wizard, self.btn_export,
                  self.btn_import):
            b.setEnabled(has)

    def selected_id(self) -> str:
        return self.selected

    # --------------------------------------------------------------- aksi
    def _target(self, cam_id: str | None) -> CameraConfig | None:
        cid = cam_id or self.selected
        if cid:
            self.select(cid)
        return self.manager.cameras.get(cid)

    def set_enabled(self, cam_id: str, on: bool) -> None:
        cam = self.manager.cameras.get(cam_id)
        if cam is None or cam.enabled == on:
            return
        cam.enabled = on
        if not on:
            self.manager.stop(cam.id)
        self.manager.save()

    def toggle_run(self, cam_id: str) -> None:
        if self.manager.is_running(cam_id):
            self.manager.stop(cam_id)
        else:
            self.manager.start(cam_id)

    def add_camera(self) -> None:
        dlg = AddCameraDialog(self.manager, parent=self)
        dlg.saved.connect(self.zones_requested)    # §5.5 langkah 5: lanjut menggambar zona
        dlg.exec()

    def edit_camera(self, cam_id: str | None = None) -> None:
        if not (cam := self._target(cam_id)):
            return
        was_running = self.manager.is_running(cam.id)
        dlg = AddCameraDialog(self.manager, cam=cam, parent=self)
        if dlg.exec() and was_running:
            self.manager.stop(cam.id)              # pakai pengaturan baru
            self.manager.start(cam.id)

    def delete_camera(self, cam_id: str | None = None) -> None:
        if not (cam := self._target(cam_id)):
            return
        if QMessageBox.question(
                self, "Hapus kamera",
                f"Hapus “{cam.name}”? Profil zonanya tetap disimpan.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.manager.stop(cam.id)
        try:
            import keyring
            keyring.delete_password(secrets.SERVICE, cam.id)
        except Exception as e:                     # tidak ada entri / keyring terkunci
            log.info("password %s tidak dihapus: %s", cam.id, e)
        self.manager.cameras.pop(cam.id, None)
        self.manager.save()

    def _request_zones(self) -> None:
        if cam_id := self.selected_id():
            self.zones_requested.emit(cam_id)

    # ------------------------------------------------------------ Fase 8
    def wizard(self, cam_id: str | None = None) -> None:
        if cam := self._target(cam_id):
            PresetWizard(self.manager, cam.id, parent=self).exec()

    def export_profile(self, cam_id: str | None = None) -> None:
        if not (cam := self._target(cam_id)):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Ekspor profil", f"{cam.id}.aioprofile", "Profil AIO-CCTV (*.aioprofile)")
        if not path:
            return
        frame = self.manager.frames.get(cam.id)      # frame terakhir bila kamera berjalan
        snap = None if frame is None else (frame.annotated if frame.blurred else frame.raw)
        out = export_profile(self.manager.profile(cam), path, snap)
        QMessageBox.information(self, "Profil diekspor",
                                f"{out}\n\nTidak berisi URL atau kredensial kamera."
                                + ("" if snap is not None else "\nSnapshot tidak disertakan "
                                   "(kamera tidak sedang berjalan)."))

    def import_profile(self, cam_id: str | None = None) -> None:
        if not (cam := self._target(cam_id)):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Impor profil", "",
                                              "Profil AIO-CCTV (*.aioprofile)")
        if not path:
            return
        try:
            imp = import_profile(path)
        except Exception as e:                     # ZIP rusak / bukan .aioprofile
            QMessageBox.warning(self, "Gagal mengimpor", str(e))
            return
        p = imp.profile
        mid = p.module.module_id
        info = self.manager.modules.get(mid)
        notes = []
        if info is None or not info.valid:
            notes.append(f"⚠ Modul “{mid}” belum terpasang/valid di PC ini.")
        elif p.module.module_version and info.manifest.version != p.module.module_version:
            notes.append(f"⚠ Versi modul berbeda: profil {p.module.module_version}, "
                         f"terpasang {info.manifest.version}.")
        if QMessageBox.question(
                self, "Impor profil",
                f"Terapkan ke kamera “{cam.name}”?\n\nModul: {mid}\n"
                f"{len(p.zones)} zona, {len(p.lines)} garis, {len(p.rules)} aturan.\n"
                f"Profil kamera ini saat ini akan DIGANTI.\n\n" + "\n".join(notes)
        ) != QMessageBox.StandardButton.Yes:
            return
        self.manager.save_profile(cam, apply_import(self.manager.profile(cam), p))
        if self.manager.is_running(cam.id):
            self.manager.stop(cam.id)
            self.manager.start(cam.id)
        QMessageBox.information(self, "Profil diimpor",
                                "Periksa posisi zona di halaman Zona: sudut kamera tujuan bisa "
                                "berbeda dari kamera asal.")
