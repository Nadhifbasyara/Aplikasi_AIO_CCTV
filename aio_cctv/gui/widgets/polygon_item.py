"""Item kanvas editor zona (Fase 5 §5.6): poligon dengan titik yang bisa di-drag
dan garis hitung dengan panah arah IN."""
import math

from PyQt6.QtCore import QLineF, QPointF, Qt
from PyQt6.QtGui import QBrush, QColor, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPolygonItem,
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
        self.setPen(QPen(QColor("#202020"), 1))
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
        self.handles: list[VertexHandle] = []
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setZValue(1)
        self.set_color(color)

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

    def set_color(self, color: str) -> None:
        self.color = color
        c = QColor(color)
        self.setPen(QPen(c, 2))
        c.setAlpha(60)
        self.setBrush(QBrush(c))

    def attach_handles(self) -> None:
        for i, p in enumerate(self.polygon()):
            self.handles.append(VertexHandle(self, i, p))

    def move_vertex(self, i: int, pos: QPointF) -> None:
        poly = self.polygon()
        poly[i] = pos
        self.setPolygon(poly)

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

    def set_color(self, color: str) -> None:
        self.color = color
        c = QColor(color)
        self.setPen(QPen(c, 3))
        self.arrow.setBrush(QBrush(c))
        self.arrow.setPen(QPen(c, 1))

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
