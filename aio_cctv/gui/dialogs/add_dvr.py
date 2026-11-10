"""Dialog Tambah DVR/NVR: satu alamat + akun, banyak channel sekaligus (Fase 9 §6, RM-2).

Alur: isi IP & akun → "Cek channel" memeriksa channel 1..N (ffprobe, paralel terbatas) →
centang channel yang dipakai → tiap channel menjadi satu kamera bernama "<DVR> CH<n>" yang
berbagi kredensial (disimpan di keyring per kamera) dan dikelompokkan lewat `device`.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from aio_cctv.core import secrets
from aio_cctv.gui import theme
from aio_cctv.gui.camera_manager import CameraConfig, build_source_uri, source_config
from aio_cctv.gui.dialogs.add_camera import new_camera_id
from aio_cctv.gui.utils import friendly_error
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.modules.registry import DEFAULT_MODULE
from aio_cctv.sources.probe import probe
from aio_cctv.sources.url_builder import BRANDS, has_channels, mask
from aio_cctv.zones.models import SourceConfig

MAX_PARALLEL_PROBE = 4          # DVR murah sering menolak banyak koneksi RTSP bersamaan


class AddDvrDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Tambah DVR / NVR")
        self.resize(640, 620)
        self._alive = True
        self._pending: list[int] = []
        self._running = 0

        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("mis. DVR Toko")
        self.cbo_brand = QComboBox()
        for key, b in BRANDS.items():
            if has_channels(key):
                self.cbo_brand.addItem(b.get("label", key), key)
        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("192.168.1.64")
        self.spin_port = QSpinBox()
        self.spin_port.setRange(0, 65535)
        self.spin_port.setSpecialValueText("bawaan merek")
        self.ed_user = QLineEdit()
        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.cbo_stream = QComboBox()
        self.cbo_stream.addItem("Sub-stream (disarankan; DVR membatasi bandwidth)", "sub")
        self.cbo_stream.addItem("Main-stream (resolusi penuh)", "main")
        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 64)
        self.spin_count.setValue(4)
        self.spin_count.setSuffix(" channel")
        self.cbo_module = QComboBox()
        for mid, info in sorted(manager.modules.items()):
            if info.valid:
                self.cbo_module.addItem(info.manifest.name, mid)
        self.cbo_module.setCurrentIndex(max(0, self.cbo_module.findData(DEFAULT_MODULE)))

        form = QFormLayout()
        form.addRow("Nama perangkat", self.ed_name)
        form.addRow("Merek", self.cbo_brand)
        form.addRow("Alamat IP", self.ed_host)
        form.addRow("Port", self.spin_port)
        form.addRow("Username", self.ed_user)
        form.addRow("Password", self.ed_pass)
        form.addRow("Stream", self.cbo_stream)
        form.addRow("Jumlah channel", self.spin_count)
        form.addRow("Modul analitik", self.cbo_module)

        self.btn_check = QPushButton("Cek channel")
        self.btn_check.clicked.connect(self.check)
        self.lbl_info = QLabel("Klik “Cek channel” untuk memeriksa setiap channel. Channel tanpa "
                               "kamera biasanya gagal dibuka — biarkan tidak dicentang.")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setStyleSheet(f"color:{theme.MUTED};")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Pakai", "Channel", "Status"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Tambahkan kamera")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)

        check_row = QHBoxLayout()
        check_row.addWidget(self.btn_check)
        check_row.addStretch()
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(check_row)
        lay.addWidget(self.lbl_info)
        lay.addWidget(self.table, 1)
        lay.addWidget(buttons)
        self.spin_count.valueChanged.connect(self._fill_rows)
        self._fill_rows()

    # ------------------------------------------------------------ channel
    def _cam(self, channel: int, cam_id: str = "__dvr__") -> CameraConfig:
        name = self.ed_name.text().strip() or "DVR"
        return CameraConfig(cam_id, f"{name} CH{channel}", self.cbo_brand.currentData(),
                            host=self.ed_host.text().strip(),
                            port=self.spin_port.value() or None, user=self.ed_user.text().strip(),
                            stream=self.cbo_stream.currentData(), channel=channel,
                            location=name, device=f"dvr:{self.ed_host.text().strip()}")

    def _fill_rows(self) -> None:
        n = self.spin_count.value()
        self.table.setRowCount(n)
        for r in range(n):
            if self.table.item(r, 0) is None:
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                chk.setCheckState(Qt.CheckState.Checked)
                self.table.setItem(r, 0, chk)
                self.table.setItem(r, 1, QTableWidgetItem(f"CH{r + 1}"))
                self.table.setItem(r, 2, QTableWidgetItem("belum dicek"))

    def check(self) -> None:
        if not self.ed_host.text().strip():
            QMessageBox.warning(self, "Belum lengkap", "Alamat IP DVR belum diisi.")
            return
        self.btn_check.setEnabled(False)
        self._pending = list(range(1, self.spin_count.value() + 1))
        for ch in self._pending:
            self._status(ch, "menunggu…", theme.MUTED)
        pool = QThreadPool.globalInstance()
        for _ in range(min(MAX_PARALLEL_PROBE, len(self._pending), pool.maxThreadCount())):
            self._next_probe()

    def _next_probe(self) -> None:
        if not self._pending:
            if self._running == 0:
                self.btn_check.setEnabled(True)
                ok = sum(self.table.item(r, 0).checkState() == Qt.CheckState.Checked
                         for r in range(self.table.rowCount()))
                self.lbl_info.setText(f"Selesai. {ok} channel siap ditambahkan.")
            return
        ch = self._pending.pop(0)
        self._running += 1
        self._status(ch, "memeriksa…", theme.MUTED)
        uri = build_source_uri(self._cam(ch), self.ed_pass.text())
        run_async(probe, uri, 8, on_done=lambda res, c=ch: self._probed(c, res),
                  on_failed=lambda msg, c=ch: self._probed(c, {"ok": False, "error": msg}))

    def _probed(self, ch: int, res: dict) -> None:
        if not self._alive:
            return
        self._running -= 1
        row = ch - 1
        if row < self.table.rowCount():
            if res.get("ok"):
                self._status(ch, f"OK — {str(res['codec']).upper()} {res['width']}×{res['height']}"
                                 f" @ {res['fps']} fps", theme.OK)
                self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            else:
                msg = friendly_error(res.get("error", ""))
                if msg.startswith("Path stream salah"):   # di DVR artinya channel tanpa kamera
                    msg = "Tidak ada video — channel kosong, atau merek/template tidak cocok"
                self._status(ch, mask(msg), theme.BAD)
                self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self._next_probe()

    def _status(self, ch: int, text: str, color: str) -> None:
        if (item := self.table.item(ch - 1, 2)) is not None:
            item.setText(text)
            item.setForeground(QColor(color))

    # ------------------------------------------------------------ simpan
    def channels(self) -> list[int]:
        return [r + 1 for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.CheckState.Checked]

    def save(self) -> None:
        name = self.ed_name.text().strip()
        if not name or not self.ed_host.text().strip():
            QMessageBox.warning(self, "Belum lengkap", "Nama perangkat dan alamat IP wajib diisi.")
            return
        chans = self.channels()
        if not chans:
            QMessageBox.warning(self, "Belum ada channel", "Centang minimal satu channel.")
            return
        taken_names = {c.name.lower() for c in self.manager.cameras.values()}
        if dup := [f"{name} CH{c}" for c in chans if f"{name} CH{c}".lower() in taken_names]:
            QMessageBox.warning(self, "Nama sudah dipakai", f"Sudah ada kamera {', '.join(dup)}. "
                                "Ganti nama perangkat.")
            return
        module_id = self.cbo_module.currentData() or DEFAULT_MODULE
        info = self.manager.modules.get(module_id)
        for ch in chans:
            cam = self._cam(ch)
            cam.id = new_camera_id(cam.name, self.manager.cameras)
            cam.profile_id = cam.id
            secrets.set_password(cam.id, self.ed_pass.text())     # hanya ke keyring
            profile = self.manager.profile(cam)
            profile.module.module_id = module_id
            profile.module.module_version = info.manifest.version if info and info.valid else ""
            profile.source = SourceConfig(**source_config(cam))
            profile.name = profile.name or cam.name
            self.manager.cameras[cam.id] = cam
            self.manager.save_profile(cam, profile)
        self.created = len(chans)
        self.accept()

    def done(self, result: int) -> None:
        self._alive = False
        super().done(result)
