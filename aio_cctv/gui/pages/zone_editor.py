"""Editor zona visual — pengganti editor OpenCV Fase 1 (Fase 5 §5.6).

Klik kiri menambah titik, klik-ganda/Enter menutup poligon, Esc membatalkan.
Titik dapat di-drag; klik kanan pada bentuk membuka menu ubah/hapus/duplikasi.
"""
import logging

import numpy as np
from PyQt6 import sip
from PyQt6.QtCore import QPoint, QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QIcon, QImage, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core.colors import PALETTE
from aio_cctv.core.paths import PROFILES_DIR
from aio_cctv.gui.utils import app_settings, to_qimage
from aio_cctv.gui.widgets.polygon_item import EditableLine, EditablePolygon
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.sources.snapshot import grab_frame
from aio_cctv.zones.models import Line, Profile, SourceConfig, Zone, to_pixels

log = logging.getLogger(__name__)
ZONE_TYPES = ["dwell", "occupancy", "presence"]
CLOSE_PX = 8                         # klik-ganda menutup poligon tanpa titik kembar


def _detect_persons(weights: str, conf: float, image: np.ndarray):
    """Dijalankan di thread pool: memuat model bisa makan beberapa detik."""
    from aio_cctv.inference.detector import Detector
    det = Detector(weights, ["person"], conf)
    return det(image).xyxy


class ZoneCanvas(QGraphicsView):
    clicked = pyqtSignal(QPointF)
    finish_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    menu_requested = pyqtSignal(object, QPoint)

    def __init__(self):
        super().__init__(QGraphicsScene())
        self.drafting = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background:#111;")

    def shape_at(self, pos: QPoint):
        item = self.itemAt(pos)
        while item is not None and not isinstance(item, (EditablePolygon, EditableLine)):
            item = item.parentItem()
        return item

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and (
                self.drafting or self.shape_at(e.pos()) is None):
            self.clicked.emit(self.mapToScene(e.pos()))
            return                       # jangan diteruskan: mencegah rubber-band
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        if self.drafting:
            self.finish_requested.emit()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_requested.emit()
        elif e.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e) -> None:
        self.menu_requested.emit(self.shape_at(e.pos()), e.globalPos())


class ZoneEditorPage(QWidget):
    def __init__(self, camera_manager):
        super().__init__()
        self.camera_manager = camera_manager
        self.mode = "zona"
        self.cam_id = ""
        self.frame_wh = (1280, 720)
        self.shapes: list = []                  # EditablePolygon | EditableLine, urut tampil
        self.draft: list[QPointF] = []
        self.draft_items: list = []
        self.preview_items: list = []
        self.snapshot: np.ndarray | None = None
        self.dirty = False
        self._needs_snapshot = False
        self.setup_ui()
        if camera_manager is not None:
            camera_manager.cameras_changed.connect(self._reload_camera_list)
            self._reload_camera_list()

    # ------------------------------------------------------------------ UI
    def setup_ui(self) -> None:
        main_layout = QHBoxLayout(self)

        self.canvas = ZoneCanvas()
        self.scene = self.canvas.scene()
        self.pix_item = self.scene.addPixmap(QPixmap())
        self.canvas.clicked.connect(self._on_click)
        self.canvas.finish_requested.connect(self._finish_draft)
        self.canvas.cancel_requested.connect(self._cancel_draft)
        self.canvas.menu_requested.connect(self._on_menu)
        self.scene.selectionChanged.connect(self._on_selection_changed)

        self.cbo_cam = QComboBox()
        self.cbo_cam.currentIndexChanged.connect(self._on_camera_changed)
        self.btn_snapshot = QPushButton("Ambil snapshot")
        self.btn_snapshot.clicked.connect(self.take_snapshot)
        self.rb_zona = QRadioButton("Zona")
        self.rb_garis = QRadioButton("Garis")
        self.rb_zona.setChecked(True)
        self.rb_zona.toggled.connect(
            lambda on: self._set_mode("zona" if on else "garis"))
        self.btn_save = QPushButton("Simpan")
        self.btn_cancel = QPushButton("Batal")
        self.btn_save.clicked.connect(self.save)
        self.btn_cancel.clicked.connect(self.discard)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Kamera:"))
        bar.addWidget(self.cbo_cam, 1)
        bar.addWidget(self.btn_snapshot)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Mode:"))
        bar.addWidget(self.rb_zona)
        bar.addWidget(self.rb_garis)
        bar.addStretch()
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_cancel)

        left = QVBoxLayout()
        left.addLayout(bar)
        left.addWidget(self.canvas, 1)
        self.lbl_hint = QLabel()
        self.lbl_hint.setStyleSheet("color:#888;")
        left.addWidget(self.lbl_hint)
        left_w = QWidget()
        left_w.setLayout(left)
        main_layout.addWidget(left_w, 3)

        main_layout.addLayout(self._side_panel(), 1)
        self._update_hint()
        self._sync_properties()

    def _side_panel(self) -> QVBoxLayout:
        self.lbl_title = QLabel("Properti Zona")
        self.lbl_title.setStyleSheet("font-weight:bold;")
        self.lst = QListWidget()
        self.lst.currentRowChanged.connect(self._on_row_changed)

        self.ed_name = QLineEdit()
        self.ed_name.editingFinished.connect(self._apply_name)
        self.cbo_type = QComboBox()
        self.cbo_type.addItems(ZONE_TYPES)
        self.cbo_type.currentTextChanged.connect(self._apply_type)
        self.btn_color = QPushButton("Pilih warna")
        self.btn_color.clicked.connect(self._apply_color)
        self.btn_flip = QPushButton("Balik arah")
        self.btn_flip.clicked.connect(self._flip_line)
        self.btn_delete = QPushButton("Hapus bentuk")
        self.btn_delete.clicked.connect(lambda: self._delete(self._current()))

        props = QGroupBox("Properti")
        form = QFormLayout(props)
        form.addRow("Nama", self.ed_name)
        form.addRow("Tipe", self.cbo_type)
        form.addRow("Warna", self.btn_color)
        form.addRow("", self.btn_flip)
        form.addRow("", self.btn_delete)

        self.btn_preview = QPushButton("Pratinjau Deteksi")
        self.btn_preview.clicked.connect(self.preview_detection)
        self.lbl_status = QLabel("Pilih kamera untuk mulai.")
        self.lbl_status.setWordWrap(True)

        panel = QVBoxLayout()
        panel.addWidget(self.lbl_title)
        panel.addWidget(QLabel("Daftar"))
        panel.addWidget(self.lst, 1)
        panel.addWidget(props)
        panel.addWidget(self.btn_preview)
        panel.addWidget(self.lbl_status)
        return panel

    # ------------------------------------------------------------- kamera
    def _reload_camera_list(self) -> None:
        current = self.cam_id
        self.cbo_cam.blockSignals(True)
        self.cbo_cam.clear()
        for cam in sorted(self.camera_manager.cameras.values(), key=lambda c: c.name.lower()):
            self.cbo_cam.addItem(cam.name, cam.id)
        idx = self.cbo_cam.findData(current)
        self.cbo_cam.setCurrentIndex(max(0, idx))
        self.cbo_cam.blockSignals(False)
        if self.cbo_cam.count() and (idx < 0 or not self.cam_id):
            self._on_camera_changed()

    def set_camera(self, cam_id: str) -> None:
        """Dipanggil halaman Kamera / dialog tambah kamera setelah kamera disimpan."""
        idx = self.cbo_cam.findData(cam_id)
        if idx >= 0 and idx != self.cbo_cam.currentIndex():
            self.cbo_cam.setCurrentIndex(idx)      # memicu _on_camera_changed
        else:
            self.cam_id = cam_id
            self.reload()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        return QMessageBox.question(
            self, "Perubahan belum disimpan",
            "Buang perubahan zona yang belum disimpan?") == QMessageBox.StandardButton.Yes

    def discard(self) -> None:
        if self._confirm_discard():
            self.reload()

    def _on_camera_changed(self) -> None:
        new_id = self.cbo_cam.currentData() or ""
        if new_id != self.cam_id and not self._confirm_discard():
            self.cbo_cam.blockSignals(True)         # kembalikan pilihan ke kamera semula
            self.cbo_cam.setCurrentIndex(max(0, self.cbo_cam.findData(self.cam_id)))
            self.cbo_cam.blockSignals(False)
            return
        self.cam_id = new_id
        self.reload()

    @property
    def _cam(self):
        if self.camera_manager is None:
            return None
        return self.camera_manager.cameras.get(self.cam_id)

    def _profile_path(self):
        cam = self._cam
        return PROFILES_DIR / f"{(cam.profile_id or cam.id) if cam else self.cam_id}.json"

    def reload(self) -> None:
        """Muat ulang profil dari disk dan buang perubahan yang belum disimpan."""
        cam = self._cam
        self.lbl_title.setText(f"Properti Zona — {cam.name}" if cam else "Properti Zona")
        self._cancel_draft()
        self._clear_preview()
        for s in self.shapes:
            self.scene.removeItem(s)
        self.shapes.clear()
        self.dirty = False
        if cam is None:
            self.lbl_status.setText("Belum ada kamera. Tambahkan dulu di halaman Kamera.")
            self._refresh_list()
            return
        # jangan menyentuh kamera selama halaman ini belum dibuka: membuka aplikasi
        # tidak boleh otomatis membuat koneksi RTSP dari halaman Zona
        self._needs_snapshot = True
        if self.isVisible():
            self.take_snapshot()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._needs_snapshot:
            self.take_snapshot()

    def take_snapshot(self) -> None:
        cam = self._cam
        if cam is None:
            return
        self._needs_snapshot = False
        self.btn_snapshot.setEnabled(False)
        self.lbl_status.setText("Mengambil snapshot…")
        uri = self.camera_manager.source_uri(cam)
        run_async(grab_frame, uri, on_done=self._on_snapshot,
                  on_failed=lambda m: self._on_snapshot(None, m))

    def _on_snapshot(self, img, err: str = "") -> None:
        self.btn_snapshot.setEnabled(True)
        if img is None:
            profile = self._load_profile()
            self.frame_wh = tuple(profile.frame_size or (1280, 720))
            self._set_background(None)
            self.lbl_status.setText(
                f"Snapshot gagal ({err or 'tidak ada frame'}). "
                f"Menggambar di atas kanvas kosong {self.frame_wh[0]}×{self.frame_wh[1]}.")
        else:
            self.snapshot = img
            h, w = img.shape[:2]
            self.frame_wh = (w, h)
            self._set_background(img)
            self.lbl_status.setText(f"Snapshot {w}×{h}. Klik kiri menambah titik, "
                                    "klik-ganda/Enter menutup, Esc batal.")
        self._rebuild_shapes()

    def _set_background(self, img) -> None:
        w, h = self.frame_wh
        if img is None:
            blank = QImage(w, h, QImage.Format.Format_RGB888)
            blank.fill(QColor("#202020"))
            pix = QPixmap.fromImage(blank)
        else:
            pix = QPixmap.fromImage(to_qimage(img))
        self.pix_item.setPixmap(pix)
        self.scene.setSceneRect(0, 0, w, h)
        self.canvas.fitInView(self.pix_item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if not self.pix_item.pixmap().isNull():
            self.canvas.fitInView(self.pix_item, Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------------- profil
    def _load_profile(self) -> Profile:
        path = self._profile_path()
        if path.exists():
            try:
                return Profile.load(path)
            except Exception as e:
                log.warning("profil %s tidak terbaca: %s", path.name, e)
        cam = self._cam
        return Profile(profile_id=path.stem,
                       source=SourceConfig(type="file", uri=cam.file_path if cam else ""))

    def _rebuild_shapes(self) -> None:
        for s in self.shapes:
            self.scene.removeItem(s)
        self.shapes.clear()
        profile = self._load_profile()
        w, h = self.frame_wh
        for z in profile.zones:
            pts = [QPointF(float(x), float(y)) for x, y in to_pixels(z.polygon, w, h)]
            self._add_shape(EditablePolygon(z.id, pts, z.color, z.name, z.type))
        for ln in profile.lines:
            (x1, y1), (x2, y2) = to_pixels([ln.p1, ln.p2], w, h)
            self._add_shape(EditableLine(ln.id, QPointF(float(x1), float(y1)),
                                         QPointF(float(x2), float(y2)), ln.color, ln.name))
        self.dirty = False
        self._refresh_list()

    def _add_shape(self, shape) -> None:
        self.scene.addItem(shape)
        shape.attach_handles()
        self.shapes.append(shape)

    # -------------------------------------------------------- menggambar
    def _set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self._cancel_draft()
        self.mode = mode
        self._update_hint()

    def _update_hint(self) -> None:
        if self.mode == "zona":
            self.lbl_hint.setText("Mode ZONA — klik kiri menambah titik, "
                                  "klik-ganda atau Enter menutup poligon, Esc membatalkan.")
        else:
            self.lbl_hint.setText("Mode GARIS — klik dua titik; panah menunjuk arah IN. "
                                  "Esc membatalkan.")

    def _on_click(self, pos: QPointF) -> None:
        if self.pix_item.pixmap().isNull():
            return
        self.draft.append(pos)
        self.canvas.drafting = True
        self._redraw_draft()
        if self.mode == "garis" and len(self.draft) == 2:
            self._finish_draft()

    def _redraw_draft(self) -> None:
        for it in self.draft_items:
            self.scene.removeItem(it)
        self.draft_items.clear()
        pen = QPen(QColor("#00E676"), 2, Qt.PenStyle.DashLine)
        if len(self.draft) > 1:
            poly = QGraphicsPolygonItem(QPolygonF(self.draft))
            poly.setPen(pen)
            poly.setBrush(QBrush(Qt.GlobalColor.transparent))
            poly.setZValue(5)
            self.scene.addItem(poly)
            self.draft_items.append(poly)
        for p in self.draft:
            dot = QGraphicsEllipseItem(p.x() - 4, p.y() - 4, 8, 8)
            dot.setBrush(QBrush(QColor("#00E676")))
            dot.setPen(QPen(QColor("#00E676")))
            dot.setZValue(6)
            self.scene.addItem(dot)
            self.draft_items.append(dot)

    def _cancel_draft(self) -> None:
        self.draft.clear()
        self.canvas.drafting = False
        self._redraw_draft()

    def _next_id(self, prefix: str) -> str:
        taken = {s.shape_id for s in self.shapes}
        i = 1
        while f"{prefix}{i}" in taken:
            i += 1
        return f"{prefix}{i}"

    def _copy_name(self, base: str) -> str:
        taken = {s.name.lower() for s in self.shapes}
        name, i = f"{base} (salinan)", 2
        while name.lower() in taken:
            name, i = f"{base} (salinan {i})", i + 1
        return name

    def _next_name(self, base: str) -> str:
        taken = {s.name.lower() for s in self.shapes}
        i = len(self.shapes) + 1
        while f"{base} {i}".lower() in taken:
            i += 1
        return f"{base} {i}"

    def _finish_draft(self) -> None:
        pts = list(self.draft)
        # klik-ganda menambah satu titik di tempat yang sama — buang duplikatnya
        if len(pts) > 1 and (pts[-1] - pts[-2]).manhattanLength() < CLOSE_PX:
            pts.pop()
        color = PALETTE[len(self.shapes) % len(PALETTE)]
        if self.mode == "zona":
            if len(pts) < 3:
                self.lbl_status.setText("Poligon butuh minimal 3 titik.")
                return
            shape = EditablePolygon(self._next_id("z"), pts, color, self._next_name("Zona"))
        else:
            if len(pts) < 2:
                return
            shape = EditableLine(self._next_id("l"), pts[0], pts[1], color,
                                 self._next_name("Garis"))
        self._cancel_draft()
        self._add_shape(shape)
        self.dirty = True
        self._refresh_list()
        self._select(shape)

    # ---------------------------------------------------------- daftar/props
    def _refresh_list(self) -> None:
        current = self._current()
        self.lst.blockSignals(True)
        self.lst.clear()
        for s in self._live_shapes():
            pix = QPixmap(12, 12)
            pix.fill(QColor(s.color))
            label = f"{s.name}  ·  {s.ztype if s.kind == 'zona' else 'counting'}"
            self.lst.addItem(QListWidgetItem(QIcon(pix), label))
        if current in self.shapes:
            self.lst.setCurrentRow(self.shapes.index(current))
        self.lst.blockSignals(False)
        self._sync_properties()

    def _live_shapes(self) -> list:
        """Saat aplikasi ditutup, scene membongkar item-nya sementara sinyal
        selectionChanged masih menembak — item yang sudah mati harus disaring."""
        alive = [s for s in self.shapes if not sip.isdeleted(s)]
        if len(alive) != len(self.shapes):
            self.shapes[:] = alive
        return alive

    def _current(self):
        sel = [s for s in self._live_shapes() if s.isSelected()]
        return sel[0] if sel else None

    def _select(self, shape) -> None:
        self.scene.clearSelection()
        if shape is not None:
            shape.setSelected(True)

    def _on_row_changed(self, row: int) -> None:
        if 0 <= row < len(self.shapes):
            self._select(self.shapes[row])

    def _on_selection_changed(self) -> None:
        cur = self._current()
        for s in self._live_shapes():
            s.show_handles(s is cur or cur is None)
        if cur is not None:
            self.lst.blockSignals(True)
            self.lst.setCurrentRow(self.shapes.index(cur))
            self.lst.blockSignals(False)
        self._sync_properties()

    def _sync_properties(self) -> None:
        cur = self._current()
        is_zone = cur is not None and cur.kind == "zona"
        for w in (self.ed_name, self.btn_color, self.btn_delete):
            w.setEnabled(cur is not None)
        self.cbo_type.setEnabled(is_zone)
        self.btn_flip.setEnabled(cur is not None and cur.kind == "garis")
        self.ed_name.blockSignals(True)
        self.cbo_type.blockSignals(True)
        self.ed_name.setText(cur.name if cur else "")
        # garis hanya punya tipe "counting"; tampilkan apa adanya, tidak bisa diubah
        items = ZONE_TYPES if is_zone or cur is None else ["counting"]
        if [self.cbo_type.itemText(i) for i in range(self.cbo_type.count())] != items:
            self.cbo_type.clear()
            self.cbo_type.addItems(items)
        self.cbo_type.setCurrentText(cur.ztype if is_zone else items[0])
        self.cbo_type.blockSignals(False)
        self.ed_name.blockSignals(False)

    def _apply_name(self) -> None:
        cur = self._current()
        name = self.ed_name.text().strip()
        if cur is None or not name or name == cur.name:
            return
        cur.name = name
        self.dirty = True
        self._refresh_list()

    def _apply_type(self, text: str) -> None:
        cur = self._current()
        if cur is not None and cur.kind == "zona" and text in ZONE_TYPES:
            cur.ztype = text
            self.dirty = True
            self._refresh_list()

    def _apply_color(self) -> None:
        cur = self._current()
        if cur is None:
            return
        color = QColorDialog.getColor(QColor(cur.color), self, "Warna bentuk")
        if color.isValid():
            cur.set_color(color.name())
            self.dirty = True
            self._refresh_list()

    def _flip_line(self) -> None:
        cur = self._current()
        if cur is not None and cur.kind == "garis":
            cur.flip()
            self.dirty = True

    def _delete(self, shape) -> None:
        if shape is None:
            return
        self.scene.removeItem(shape)
        self.shapes.remove(shape)
        self.dirty = True
        self._refresh_list()

    def _duplicate(self, shape) -> None:
        if shape is None:
            return
        off = QPointF(20, 20)
        if shape.kind == "zona":
            pts = [shape.mapToScene(p) + off for p in shape.polygon()]
            new = EditablePolygon(self._next_id("z"), pts, shape.color,
                                  self._copy_name(shape.name), shape.ztype)
        else:
            ln = shape.line()
            new = EditableLine(self._next_id("l"), shape.mapToScene(ln.p1()) + off,
                               shape.mapToScene(ln.p2()) + off, shape.color,
                               self._copy_name(shape.name))
        self._add_shape(new)
        self.dirty = True
        self._refresh_list()
        self._select(new)

    def _on_menu(self, shape, global_pos: QPoint) -> None:
        if shape is None:
            return
        self._select(shape)
        menu = QMenu(self)
        menu.addAction("Ubah nama…", lambda: self._rename(shape))
        if shape.kind == "zona":
            sub = menu.addMenu("Ubah tipe")
            for t in ZONE_TYPES:
                sub.addAction(t, lambda t=t: self._apply_type(t))
        else:
            menu.addAction("Balik arah IN", self._flip_line)
        menu.addAction("Warna…", self._apply_color)
        menu.addAction("Duplikasi", lambda: self._duplicate(shape))
        menu.addSeparator()
        menu.addAction("Hapus", lambda: self._delete(shape))
        menu.exec(global_pos)

    def _rename(self, shape) -> None:
        name, ok = QInputDialog.getText(self, "Ubah nama", "Nama baru:", text=shape.name)
        if ok and name.strip():
            shape.name = name.strip()
            self.dirty = True
            self._refresh_list()

    # --------------------------------------------------------- pratinjau
    def _clear_preview(self) -> None:
        for it in self.preview_items:
            self.scene.removeItem(it)
        self.preview_items.clear()

    def preview_detection(self) -> None:
        if self.snapshot is None:
            self.lbl_status.setText("Ambil snapshot dulu sebelum pratinjau deteksi.")
            return
        self.btn_preview.setEnabled(False)
        self.lbl_status.setText("Menjalankan detektor sekali pada snapshot…")
        s = app_settings()
        run_async(_detect_persons, s["weights"], s["conf"], self.snapshot,
                  on_done=self._on_preview, on_failed=self._on_preview_failed)

    def _on_preview(self, boxes) -> None:
        self.btn_preview.setEnabled(True)
        self._clear_preview()
        pen = QPen(QColor("#00E5FF"), 2)
        for x1, y1, x2, y2 in boxes:
            rect = QGraphicsRectItem(x1, y1, x2 - x1, y2 - y1)
            rect.setPen(pen)
            rect.setZValue(4)
            self.scene.addItem(rect)
            self.preview_items.append(rect)
            # titik kaki (BOTTOM_CENTER) — jangkar yang dipakai zona & garis
            foot = QGraphicsEllipseItem((x1 + x2) / 2 - 5, y2 - 5, 10, 10)
            foot.setBrush(QBrush(QColor("#FFEB3B")))
            foot.setPen(QPen(QColor("#F57F17"), 1))
            foot.setZValue(5)
            self.scene.addItem(foot)
            self.preview_items.append(foot)
        self.lbl_status.setText(
            f"{len(boxes)} orang terdeteksi. Titik kuning = jangkar kaki; "
            "pastikan zona menutupinya.")

    def _on_preview_failed(self, msg: str) -> None:
        self.btn_preview.setEnabled(True)
        self.lbl_status.setText(f"Pratinjau gagal: {msg}")

    # ------------------------------------------------------------- simpan
    def _validate(self) -> str:
        from shapely.geometry import Polygon

        names = {}
        for s in self.shapes:
            key = s.name.strip().lower()
            if not key:
                return "Ada bentuk tanpa nama."
            if key in names:
                return f"Nama “{s.name}” dipakai lebih dari satu bentuk."
            names[key] = s
            if s.kind != "zona":
                continue
            pts = [(p.x(), p.y()) for p in s.scene_points()]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) < 3:
                return f"Zona “{s.name}” punya kurang dari 3 titik."
            if not Polygon(pts).is_valid:
                return f"Zona “{s.name}” saling berpotongan sendiri — perbaiki bentuknya."
        return ""

    def _collect(self) -> Profile:
        w, h = self.frame_wh
        profile = self._load_profile()
        profile.frame_size = (w, h)
        profile.zones = [Zone(id=s.shape_id, name=s.name, type=s.ztype,
                              polygon=s.normalized(w, h), color=s.color)
                         for s in self.shapes if s.kind == "zona"]
        profile.lines = []
        for s in self.shapes:
            if s.kind == "garis":
                p1, p2 = s.normalized(w, h)
                profile.lines.append(Line(id=s.shape_id, name=s.name, p1=p1, p2=p2,
                                          color=s.color))
        return profile

    def save(self) -> bool:
        if self._cam is None:
            return False
        if err := self._validate():
            QMessageBox.warning(self, "Tidak bisa disimpan", err)
            return False
        try:
            profile = self._collect()
        except Exception as e:                     # mis. titik keluar dari area gambar
            QMessageBox.warning(self, "Tidak bisa disimpan", str(e))
            return False
        profile.save(self._profile_path())
        self.dirty = False
        cam = self._cam
        if cam.profile_id != self._profile_path().stem:
            cam.profile_id = self._profile_path().stem
            self.camera_manager.save()
        else:
            self.camera_manager.cameras_changed.emit()
        # terapkan ke kamera yang sedang berjalan: restart worker (§5.6)
        restarted = ""
        if self.camera_manager.is_running(self.cam_id):
            self.camera_manager.stop(self.cam_id)
            self.camera_manager.start(self.cam_id)
            restarted = " Worker kamera dijalankan ulang."
        self.lbl_status.setText(
            f"Tersimpan: {len(profile.zones)} zona, {len(profile.lines)} garis.{restarted}")
        return True
