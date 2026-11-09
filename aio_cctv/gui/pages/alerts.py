"""Halaman Alert: riwayat alert + editor aturan per kamera (Fase 7 §3.5)."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.alerts.rules import DATA_TYPES, TYPE_LABELS, Rule, default_rules
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.storage import queries as q

RANGES = [("Hari ini", 1), ("7 hari", 7), ("30 hari", 30), ("90 hari", 90)]
SEVERITY_COLOR = {"info": "#1E88E5", "warning": "#FB8C00", "critical": "#E53935"}
ALL_ZONES = "(semua zona / tingkat kamera)"


class AlertHistory(QWidget):
    unread_changed = pyqtSignal(int)

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.rows: list[dict] = []
        self.cbo_cam = QComboBox()
        self.cbo_range = QComboBox()
        for label, days in RANGES:
            self.cbo_range.addItem(label, days)
        self.cbo_range.setCurrentIndex(1)
        self.chk_unread = QCheckBox("Hanya yang belum dibaca")
        self.btn_ack = QPushButton("Tandai dibaca")
        self.btn_ack_all = QPushButton("Tandai semua dibaca")
        for w in (self.cbo_cam, self.cbo_range):
            w.currentIndexChanged.connect(self.refresh)
        self.chk_unread.toggled.connect(self.refresh)
        self.btn_ack.clicked.connect(lambda: self._ack(selected=True))
        self.btn_ack_all.clicked.connect(lambda: self._ack(selected=False))

        bar = QHBoxLayout()
        for w in (QLabel("Kamera:"), self.cbo_cam, QLabel("Rentang:"), self.cbo_range,
                  self.chk_unread):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(self.btn_ack)
        bar.addWidget(self.btn_ack_all)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Waktu", "Kamera", "Aturan", "Tingkat", "Pesan",
                                              "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.table, 1)
        self.reload_cameras()

    def reload_cameras(self) -> None:
        current = self.cbo_cam.currentData()
        self.cbo_cam.blockSignals(True)
        self.cbo_cam.clear()
        self.cbo_cam.addItem("Semua kamera", None)
        for cam in sorted(self.manager.cameras.values(), key=lambda c: c.name.lower()):
            self.cbo_cam.addItem(cam.name, cam.id)
        self.cbo_cam.setCurrentIndex(max(0, self.cbo_cam.findData(current)))
        self.cbo_cam.blockSignals(False)

    def refresh(self) -> None:
        start, end = q.day_range(self.cbo_range.currentData() or 7)
        db, cam, unread_only = self.manager.db_path, self.cbo_cam.currentData(), \
            self.chk_unread.isChecked()               # baca widget di thread GUI, bukan di pool
        run_async(lambda: (q.alerts(db, start, end, cam, unread_only),
                           len(q.alerts(db, 0, end + 60, None, True))),
                  on_done=self._show)

    def _show(self, result) -> None:
        self.rows, unread = result
        names = {c.id: c.name for c in self.manager.cameras.values()}
        self.table.setRowCount(len(self.rows))
        for r, a in enumerate(self.rows):
            vals = [datetime.fromtimestamp(a["ts"]).strftime("%d-%m-%Y %H:%M:%S"),
                    names.get(a["camera_id"], a["camera_id"]), a["rule_id"], a["severity"],
                    a["message"], "dibaca" if a["acknowledged"] else "BARU"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setData(Qt.ItemDataRole.UserRole, a["id"])
                if c == 3:
                    item.setForeground(QColor(SEVERITY_COLOR.get(a["severity"], "#888")))
                if c == 5 and not a["acknowledged"]:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.unread_changed.emit(unread)

    def _ack(self, selected: bool) -> None:
        if selected:
            ids = sorted({self.table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)
                          for i in self.table.selectedIndexes()})
        else:
            ids = [a["id"] for a in self.rows if not a["acknowledged"]]
        if ids:
            q.ack_alerts(self.manager.db_path, ids)
            self.refresh()


class RuleEditor(QWidget):
    """Daftar aturan kamera di kiri, formulir aturan terpilih di kanan."""

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.rules: list[dict] = []
        self.dirty = False
        self._binding = False

        self.cbo_cam = QComboBox()
        self.cbo_cam.currentIndexChanged.connect(self._load_camera)
        self.lst = QListWidget()
        self.lst.currentRowChanged.connect(self._show_rule)
        self.btn_add = QPushButton("Tambah aturan")
        self.btn_del = QPushButton("Hapus")
        self.btn_default = QPushButton("Kembalikan bawaan")
        self.btn_save = QPushButton("Simpan aturan")
        self.btn_add.clicked.connect(self._add)
        self.btn_del.clicked.connect(self._delete)
        self.btn_default.clicked.connect(self._defaults)
        self.btn_save.clicked.connect(self.save)
        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)

        self.chk_enabled = QCheckBox("Aktif")
        self.cbo_type = QComboBox()
        for t, label in TYPE_LABELS.items():
            self.cbo_type.addItem(label, t)
        self.cbo_target = QComboBox()
        self.cbo_role = QComboBox()
        self.spin_thr = QDoubleSpinBox()
        self.spin_thr.setRange(-1e9, 1e9)
        self.spin_thr.setDecimals(2)
        self.spin_for = self._seconds()
        self.spin_window = self._seconds(1)
        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 10_000)
        self.spin_cooldown = self._seconds()
        self.cbo_sev = QComboBox()
        self.cbo_sev.addItems(["info", "warning", "critical"])
        self.ed_msg = QLineEdit()
        self.ed_msg.setPlaceholderText("kosong = deskripsi otomatis")

        self.form = QFormLayout()
        for label, w in (("", self.chk_enabled), ("Jenis", self.cbo_type),
                         ("Metrik / event", self.cbo_target), ("Peran zona", self.cbo_role),
                         ("Ambang", self.spin_thr), ("Bertahan selama", self.spin_for),
                         ("Jendela event", self.spin_window), ("Minimal kejadian", self.spin_count),
                         ("Jeda antar-alert", self.spin_cooldown), ("Tingkat", self.cbo_sev),
                         ("Pesan", self.ed_msg)):
            self.form.addRow(label, w)
        for w in (self.chk_enabled,):
            w.toggled.connect(self._apply)
        for w in (self.cbo_type, self.cbo_target, self.cbo_role, self.cbo_sev):
            w.currentIndexChanged.connect(self._apply)
        for w in (self.spin_thr, self.spin_for, self.spin_window, self.spin_count,
                  self.spin_cooldown):
            w.valueChanged.connect(self._apply)
        self.ed_msg.editingFinished.connect(self._apply)

        left = QVBoxLayout()
        left.addWidget(self.lst, 1)
        btns = QHBoxLayout()
        for b in (self.btn_add, self.btn_del, self.btn_default):
            btns.addWidget(b)
        left.addLayout(btns)
        right = QVBoxLayout()
        right.addLayout(self.form)
        right.addStretch()
        right.addWidget(self.btn_save)
        right.addWidget(self.lbl_status)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(right, 1)
        top = QHBoxLayout()
        top.addWidget(QLabel("Kamera:"))
        top.addWidget(self.cbo_cam, 1)
        hint = QLabel("Aturan metrik/event dievaluasi tiap frame di thread kamera; aturan kamera "
                      "offline/modul error/FPS dievaluasi aplikasi tiap 5 detik. Perubahan berlaku "
                      "setelah disimpan (kamera yang berjalan dijalankan ulang).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(hint)
        lay.addLayout(body, 1)
        self.reload_cameras()

    @staticmethod
    def _seconds(minimum: float = 0) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(minimum, 86400)
        s.setDecimals(1)
        s.setSuffix(" detik")
        return s

    # ------------------------------------------------------------- kamera
    def reload_cameras(self) -> None:
        current = self.cbo_cam.currentData()
        self.cbo_cam.blockSignals(True)
        self.cbo_cam.clear()
        for cam in sorted(self.manager.cameras.values(), key=lambda c: c.name.lower()):
            self.cbo_cam.addItem(cam.name, cam.id)
        self.cbo_cam.setCurrentIndex(max(0, self.cbo_cam.findData(current)))
        self.cbo_cam.blockSignals(False)
        self._load_camera()

    def _manifest(self):
        cam = self.manager.cameras.get(self.cbo_cam.currentData())
        info = self.manager.module_for(cam) if cam else None
        return info.manifest if info is not None and info.valid else None

    def _load_camera(self) -> None:
        cam = self.manager.cameras.get(self.cbo_cam.currentData())
        self.rules = [dict(r) for r in cam.rules] if cam and cam.rules else \
            [r.model_dump() for r in default_rules()]
        self.dirty = False
        self.lbl_status.setText("" if cam else "Belum ada kamera.")
        self._refresh_list(0)

    def _refresh_list(self, select: int | None = None) -> None:
        row = self.lst.currentRow() if select is None else select
        self.lst.blockSignals(True)
        self.lst.clear()
        for r in self.rules:
            try:
                text = Rule.model_validate(r).describe()
            except Exception:                      # aturan setengah jadi saat diedit
                text = r.get("type", "?")
            self.lst.addItem(("" if r.get("enabled", True) else "[nonaktif] ") + text)
        self.lst.blockSignals(False)
        self.lst.setCurrentRow(min(max(row, 0), len(self.rules) - 1))
        self._show_rule(self.lst.currentRow())

    # ------------------------------------------------------------- formulir
    def _fill_targets(self, rtype: str, current: str | None) -> None:
        m = self._manifest()
        self.cbo_target.clear()
        if rtype in ("metric_gt", "metric_lt") and m:
            for spec in m.metrics:
                self.cbo_target.addItem(f"{spec.label or spec.key} ({spec.key})", spec.key)
        elif rtype == "event" and m:
            for e in m.events:
                self.cbo_target.addItem(f"{e.label or e.type} ({e.type})", e.type)
        if current and self.cbo_target.findData(current) < 0:
            self.cbo_target.addItem(f"{current} (tidak dikenal modul)", current)
        self.cbo_target.setCurrentIndex(max(0, self.cbo_target.findData(current)))
        self.cbo_role.clear()
        self.cbo_role.addItem(ALL_ZONES, None)
        for r in (m.regions if m else []):
            self.cbo_role.addItem(r.label or r.role, r.role)

    def _show_rule(self, i: int) -> None:
        has = 0 <= i < len(self.rules)
        for k in range(self.form.rowCount()):
            self.form.itemAt(k, QFormLayout.ItemRole.FieldRole).widget().setEnabled(has)
        self.btn_del.setEnabled(has)
        if not has:
            return
        r = Rule.model_validate({**self.rules[i]})
        self._binding = True
        self.chk_enabled.setChecked(r.enabled)
        self.cbo_type.setCurrentIndex(self.cbo_type.findData(r.type))
        self._fill_targets(r.type, r.metric or r.event)
        self.cbo_role.setCurrentIndex(max(0, self.cbo_role.findData(r.region_role)))
        self.spin_thr.setValue(r.threshold or 0.0)
        self.spin_for.setValue(r.for_s)
        self.spin_window.setValue(r.window_s)
        self.spin_count.setValue(r.min_count)
        self.spin_cooldown.setValue(r.cooldown_s)
        self.cbo_sev.setCurrentText(r.severity)
        self.ed_msg.setText(r.message)
        self._binding = False
        self._sync_visibility(r.type)

    def _sync_visibility(self, rtype: str) -> None:
        data = rtype in DATA_TYPES
        uses_thr = rtype in ("metric_gt", "metric_lt", "overload")
        for w, on in ((self.cbo_target, data), (self.cbo_role, data), (self.spin_thr, uses_thr),
                      (self.spin_window, rtype == "event"), (self.spin_count, rtype == "event")):
            w.setEnabled(on and self.lst.currentRow() >= 0)

    def _apply(self, *_args) -> None:
        i = self.lst.currentRow()
        if self._binding or not 0 <= i < len(self.rules):
            return
        rtype = self.cbo_type.currentData()
        if rtype != self.rules[i].get("type"):              # jenis berubah: isi ulang pilihan
            self._binding = True
            self._fill_targets(rtype, None)
            self._binding = False
        target = self.cbo_target.currentData() if rtype in DATA_TYPES else None
        self.rules[i] = {
            "id": self.rules[i]["id"], "type": rtype, "enabled": self.chk_enabled.isChecked(),
            "metric": target if rtype in ("metric_gt", "metric_lt") else None,
            "event": target if rtype == "event" else None,
            "region_role": self.cbo_role.currentData() if rtype in DATA_TYPES else None,
            "threshold": self.spin_thr.value() if rtype in ("metric_gt", "metric_lt", "overload")
            else None,
            "for_s": self.spin_for.value(), "window_s": self.spin_window.value(),
            "min_count": self.spin_count.value(), "cooldown_s": self.spin_cooldown.value(),
            "severity": self.cbo_sev.currentText(), "message": self.ed_msg.text().strip(),
        }
        self.dirty = True
        self._sync_visibility(rtype)
        item = self.lst.item(i)
        try:
            item.setText(("" if self.rules[i]["enabled"] else "[nonaktif] ")
                         + Rule.model_validate(self.rules[i]).describe())
        except Exception:
            item.setText(rtype)

    def _add(self) -> None:
        taken = {r["id"] for r in self.rules}
        n = 1
        while f"r{n}" in taken:
            n += 1
        m = self._manifest()
        metric = m.metrics[0].key if m and m.metrics else None
        self.rules.append({"id": f"r{n}", "type": "metric_gt" if metric else "camera_offline",
                           "metric": metric, "threshold": 1.0, "for_s": 10, "cooldown_s": 300})
        self.dirty = True
        self._refresh_list(len(self.rules) - 1)

    def _delete(self) -> None:
        i = self.lst.currentRow()
        if 0 <= i < len(self.rules):
            del self.rules[i]
            self.dirty = True
            self._refresh_list(i)

    def _defaults(self) -> None:
        self.rules = [r.model_dump() for r in default_rules()]
        self.dirty = True
        self._refresh_list(0)

    def save(self) -> bool:
        cam = self.manager.cameras.get(self.cbo_cam.currentData())
        if cam is None:
            return False
        try:
            rules = [Rule.model_validate(r) for r in self.rules]
        except Exception as e:
            QMessageBox.warning(self, "Aturan tidak valid", str(e))
            return False
        problems = [r.id for r in rules if r.type in DATA_TYPES and not (r.metric or r.event)]
        if problems:
            QMessageBox.warning(self, "Aturan belum lengkap",
                                f"Aturan {', '.join(problems)} belum memilih metrik/event.")
            return False
        cam.rules = [r.model_dump(exclude_defaults=True) | {"id": r.id, "type": r.type}
                     for r in rules]
        self.manager.save()
        self.dirty = False
        note = ""
        if self.manager.is_running(cam.id):
            self.manager.stop(cam.id)
            self.manager.start(cam.id)
            note = " Kamera dijalankan ulang."
        self.lbl_status.setText(f"{len(rules)} aturan tersimpan.{note}")
        return True


class AlertsPage(QWidget):
    unread_changed = pyqtSignal(int)

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.history = AlertHistory(manager)
        self.editor = RuleEditor(manager)
        self.history.unread_changed.connect(self.unread_changed)
        tabs = QTabWidget()
        tabs.addTab(self.history, "Riwayat")
        tabs.addTab(self.editor, "Aturan")
        QVBoxLayout(self).addWidget(tabs)
        manager.cameras_changed.connect(self._cameras_changed)
        manager.modules_changed.connect(self.editor._load_camera)
        # alert baru bisa datang beruntun: segarkan paling cepat tiap 1 detik
        self._debounce = QTimer(self, singleShot=True, interval=1000)
        self._debounce.timeout.connect(self.history.refresh)
        manager.alert_raised.connect(lambda *_: self._debounce.start())
        self.history.refresh()

    def _cameras_changed(self) -> None:
        self.history.reload_cameras()
        if not self.editor.dirty:
            self.editor.reload_cameras()
