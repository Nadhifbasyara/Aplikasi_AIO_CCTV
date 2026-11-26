"""Panel "Aktivitas Terbaru" di halaman Live: event modul + alert semua kamera, terbaru di atas."""
from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout

from aio_cctv.gui import theme

MAX_ITEMS = 200
DIRECTION = {"in": "masuk", "out": "keluar"}


def describe_event(e, event_labels: dict[str, str], region_names: dict[str, str]) -> str:
    """Event modul → kalimat singkat, mis. "Sesi dwell selesai di Meja belajar (12 dtk)"."""
    text = event_labels.get(e.type) or e.type.replace("_", " ").capitalize()
    if e.region_id and (name := region_names.get(e.region_id)):
        text += f" di {name}"
    data = e.data or {}
    if "direction" in data:
        text += f" ({DIRECTION.get(data['direction'], data['direction'])})"
    elif "duration_s" in data:
        text += f" ({float(data['duration_s']):.0f} dtk)"
    return text


class ActivityLog(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent, objectName="activityPanel")
        self.setStyleSheet(
            f"QFrame#activityPanel {{ background:{theme.SURFACE}; border:1px solid "
            f"{theme.BORDER_STRONG}; border-radius:8px; }}"
            "QListWidget { background:transparent; border:none; }"
            "QListWidget::item { padding:3px 2px; border:none; }")
        title = QLabel("Aktivitas Terbaru")
        title.setStyleSheet("font-size:15px; font-weight:bold; border:none;")
        sub = QLabel("Live Log")
        sub.setStyleSheet(f"color:{theme.MUTED}; border:none;")
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch()
        head.addWidget(sub)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.empty = QLabel("Belum ada aktivitas.")
        self.empty.setStyleSheet(f"color:{theme.MUTED}; border:none;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addLayout(head)
        lay.addWidget(self.empty)
        lay.addWidget(self.list, 1)

    def add(self, text: str, epoch: float | None = None, color: str | None = None) -> None:
        stamp = datetime.fromtimestamp(epoch or time.time()).astimezone().strftime("%H:%M:%S")
        label = QLabel(f"<span style='color:{theme.MUTED}'>{stamp} -</span> "
                       f"<span style='color:{color or theme.TEXT}'>{text}</span>")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setStyleSheet("border:none; background:transparent;")
        item = QListWidgetItem()
        self.list.insertItem(0, item)                     # terbaru di atas
        self.list.setItemWidget(item, label)
        item.setSizeHint(QSize(0, self._height(label)))
        while self.list.count() > MAX_ITEMS:
            self.list.takeItem(self.list.count() - 1)
        self.empty.hide()

    def _height(self, label: QLabel) -> int:
        """Tinggi baris = tinggi teks setelah dibungkus selebar panel (bukan sizeHint satu baris)."""
        return label.heightForWidth(max(120, self.list.viewport().width() - 6)) + 8

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if (label := self.list.itemWidget(item)) is not None:
                item.setSizeHint(QSize(0, self._height(label)))
