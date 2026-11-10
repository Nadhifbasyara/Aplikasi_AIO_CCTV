"""Item kanvas editor zona (Fase 5 §5.6): poligon dengan titik yang bisa di-drag
dan garis hitung dengan panah arah IN."""
import math

from PyQt6.QtCore import QLineF, QPointF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPen, QPolygonF, QTransform
from PyQt6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPolygonItem,
    QGraphicsSimpleTextItem,
    QStyle,
)

ARROW = 18


class VertexHandle(QGraphicsEllipseItem):
    R = 6

    def __init__(self, owner: QGraphicsItem, index: int, pos: QPointF):
        # anak dari owner: koordinatnya otomatis ikut sistem koordinat bentuk induk
        super().__init__(-self.R, -self.R, 2 * self.R, 2 * self.R, owner)
        self.owner, self.index = owner, index
        self.setPos(pos)
        self.setBrush(QBrush(QColor("white")))
        self.setPen(QPen(QColor(getattr(owner, "color", "#202020")), 2))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setZValue(10)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.owner.move_vertex(self.index, self.pos())
        return super().itemChange(change, value)


class _Shape:
    """Perilaku yang dipakai bersama poligon dan garis."""

    def _init_shape(self, shape_id: str, name: str, color: str) -> None:
        self.shape_id, self.name = shape_id, name or shape_id
        self.role: str | None = None             # peran dari manifest modul (Fase 6)
        self.handles: list[VertexHandle] = []
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setZValue(1)
        # label nama di kanvas: ukuran tetap di layar walau gambar diperkecil/diperbesar
        self.label = QGraphicsSimpleTextItem(self.name, self)
        self.label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.label.setBrush(QBrush(QColor("white")))
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        self.label.setFont(font)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(6)
        shadow.setOffset(0, 1)
        shadow.setColor(QColor(0, 0, 0, 220))
        self.label.setGraphicsEffect(shadow)
        self.label.setZValue(3)
        self.set_color(color)
        self._place_label()

    def set_name(self, name: str) -> None:
        self.name = name
        self.label.setText(name)
        self._place_label()

    def _center_label_at(self, pos: QPointF, dy: float = 0.0) -> None:
        r = self.label.boundingRect()
        self.label.setPos(pos)                   # titik jangkar (koordinat bentuk)
        self.label.setTransform(QTransform.fromTranslate(-r.width() / 2, -r.height() / 2 + dy))

    @staticmethod
    def _unselected(option):
        # tanpa bingkai seleksi putus-putus bawaan Qt: bentuk terpilih ditandai titik sudutnya
        option.state &= ~QStyle.StateFlag.State_Selected
        return option

    def show_handles(self, visible: bool) -> None:
        for h in self.handles:
            h.setVisible(visible)

    def _norm(self, points: list[QPointF], w: int, h: int) -> list[tuple[float, float]]:
        # mapToScene: benar walaupun bentuknya digeser utuh atau view di-zoom
        return [(round(self.mapToScene(p).x() / (w - 1), 4),
                 round(self.mapToScene(p).y() / (h - 1), 4)) for p in points]


class EditablePolygon(QGraphicsPolygonItem, _Shape):
    kind = "zona"

    def __init__(self, zone_id: str, points: list[QPointF], color: str,
                 name: str = "", ztype: str = "dwell"):
        super().__init__(QPolygonF(points))
        self.ztype = ztype
        self._init_shape(zone_id, name, color)

    @property
    def zone_id(self) -> str:
        return self.shape_id

    def paint(self, painter, option, widget=None) -> None:
        QGraphicsPolygonItem.paint(self, painter, self._unselected(option), widget)

    def set_color(self, color: str) -> None:
        self.color = color
        c = QColor(color)
        self.setPen(QPen(c, 2))
        c.setAlpha(70)
        self.setBrush(QBrush(c))
        for h in self.handles:
            h.setPen(QPen(QColor(color), 2))

    def _place_label(self) -> None:
        self._center_label_at(self.polygon().boundingRect().center())

    def attach_handles(self) -> None:
        for i, p in enumerate(self.polygon()):
            self.handles.append(VertexHandle(self, i, p))

    def move_vertex(self, i: int, pos: QPointF) -> None:
        poly = self.polygon()
        poly[i] = pos
        self.setPolygon(poly)
        self._place_label()

    def scene_points(self) -> list[QPointF]:
        return [self.mapToScene(p) for p in self.polygon()]

    def normalized(self, w: int, h: int) -> list[tuple[float, float]]:
        return self._norm(list(self.polygon()), w, h)


class EditableLine(QGraphicsLineItem, _Shape):
    kind = "garis"

    def __init__(self, line_id: str, p1: QPointF, p2: QPointF, color: str, name: str = ""):
        super().__init__(QLineF(p1, p2))
        self.arrow = QGraphicsPolygonItem(self)      # penunjuk sisi IN
        self.arrow.setZValue(2)
        self._syncing = False
        self._init_shape(line_id, name, color)
        self._update_arrow()

    @property
    def line_id(self) -> str:
        return self.shape_id

    def paint(self, painter, option, widget=None) -> None:
        QGraphicsLineItem.paint(self, painter, self._unselected(option), widget)

    def set_color(self, color: str) -> None:
        self.color = color
        c = QColor(color)
        self.setPen(QPen(c, 3))
        self.arrow.setBrush(QBrush(c))
        self.arrow.setPen(QPen(c, 1))
        for h in self.handles:
            h.setPen(QPen(c, 2))

    def _place_label(self) -> None:
        if not hasattr(self, "arrow"):
            return
        ln = self.line()                          # di samping titik tengah, sisi seberang panah
        self._center_label_at(QPointF((ln.x1() + ln.x2()) / 2, (ln.y1() + ln.y2()) / 2), dy=-16)

    def attach_handles(self) -> None:
        ln = self.line()
        for i, p in enumerate((ln.p1(), ln.p2())):
            self.handles.append(VertexHandle(self, i, p))
        self._update_arrow()

    def move_vertex(self, i: int, pos: QPointF) -> None:
        if self._syncing:
            return
        ln = self.line()
        self.setLine(QLineF(pos, ln.p2()) if i == 0 else QLineF(ln.p1(), pos))
        self._update_arrow()
        self._place_label()

    def flip(self) -> None:
        """Tukar p1/p2 — di sv.LineZone ini membalik arti IN/OUT."""
        ln = self.line()
        self.setLine(QLineF(ln.p2(), ln.p1()))
        self._syncing = True
        for h, p in zip(self.handles, (self.line().p1(), self.line().p2())):
            h.setPos(p)
        self._syncing = False
        self._update_arrow()

    def _update_arrow(self) -> None:
        ln = self.line()
        length = math.hypot(ln.dx(), ln.dy()) or 1.0
        ux, uy = ln.dx() / length, ln.dy() / length
        nx, ny = uy, -ux                    # sisi IN, diverifikasi terhadap sv.LineZone
        mid = QPointF((ln.x1() + ln.x2()) / 2, (ln.y1() + ln.y2()) / 2)
        tip = mid + QPointF(nx * ARROW, ny * ARROW)
        left = mid + QPointF(ux * ARROW * 0.4, uy * ARROW * 0.4)
        right = mid - QPointF(ux * ARROW * 0.4, uy * ARROW * 0.4)
        self.arrow.setPolygon(QPolygonF([tip, left, right]))

    def normalized(self, w: int, h: int) -> list[tuple[float, float]]:
        ln = self.line()
        return self._norm([ln.p1(), ln.p2()], w, h)
