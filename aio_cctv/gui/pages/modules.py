"""Halaman Modul: daftar, pasang, dan uji paket modul analitik (Fase 6 §5.6)."""
import hashlib
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aio_cctv.core.paths import BUNDLED_MODULES_DIR, USER_MODULES_DIR
from aio_cctv.gui.workers.net_tasks import run_async
from aio_cctv.modules import registry
from aio_cctv.modules.check import run_checks

COLS = ["Nama", "ID", "Versi", "Penulis", "Model", "Lokasi", "Status"]
TRUST_NOTE = ("Modul menjalankan kode Python di komputer ini. Pasang hanya paket dari tim "
              "atau sumber yang Anda percaya.")


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


class ModulesPage(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._testing = False
        self._setup_ui()
        manager.modules_changed.connect(self.refresh)
        manager.cameras_changed.connect(self.refresh)
        self.refresh()

    def _setup_ui(self) -> None:
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._show_detail)

        self.btn_folder = QPushButton("Pasang dari folder…")
        self.btn_zip = QPushButton("Pasang dari ZIP…")
        self.btn_reload = QPushButton("Muat ulang")
        self.btn_test = QPushButton("Uji modul")
        self.btn_folder.clicked.connect(lambda: self._install(zip_file=False))
        self.btn_zip.clicked.connect(lambda: self._install(zip_file=True))
        self.btn_reload.clicked.connect(self.manager.reload_modules)
        self.btn_test.clicked.connect(self._test)

        bar = QHBoxLayout()
        for b in (self.btn_folder, self.btn_zip, self.btn_reload, self.btn_test):
            bar.addWidget(b)
        bar.addStretch()

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setStyleSheet("font-family:monospace;")
        hint = QLabel(f"Modul bawaan: {BUNDLED_MODULES_DIR}\nModul terpasang: {USER_MODULES_DIR}\n"
                      "Kontrak pembuatan modul: docs/kontrak-modul.md")
        hint.setStyleSheet("color:#888;")
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([300, 300])

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(split, 1)
        lay.addWidget(hint)

    # ------------------------------------------------------------------ isi
    def refresh(self) -> None:
        mods = sorted(self.manager.modules.values(), key=lambda i: i.id)
        current = self._selected()
        self.table.setRowCount(len(mods))
        for r, info in enumerate(mods):
            m = info.manifest
            used = sum(self.manager.module_id(c) == info.id for c in self.manager.cameras.values())
            values = [
                m.name if m else info.path.name,
                info.id,
                m.version if m else "—",
                ", ".join(m.authors) if m else "—",
                f"{Path(m.model.weights).name} [{', '.join(m.model.classes) or 'semua kelas'}]"
                if m else "—",
                "bawaan" if info.path.is_relative_to(BUNDLED_MODULES_DIR) else "terpasang",
                ("valid" if info.valid else "TIDAK VALID") + (f" · {used} kamera" if used else ""),
            ]
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, info.id)
                if c == len(COLS) - 1 and not info.valid:
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for r in range(self.table.rowCount()):
            if self.table.item(r, 1).text() == current:
                self.table.selectRow(r)
        self.btn_test.setEnabled(bool(mods) and not self._testing)
        self._show_detail()

    def _selected(self) -> str:
        item = self.table.item(self.table.currentRow(), 1)
        return item.text() if item else ""

    def _show_detail(self) -> None:
        info = self.manager.modules.get(self._selected())
        if info is None:
            self.detail.setPlainText("Pilih modul untuk melihat rinciannya.")
            return
        m, lines = info.manifest, [f"Folder: {info.path}"]
        if info.errors:
            lines += ["", "Masalah:"] + [f"  - {e}" for e in info.errors]
        if m is not None:
            lines += ["", f"{m.name} — {m.id} {m.version} (kontrak api {m.api_version})",
                      f"Penulis: {', '.join(m.authors) or '-'} · Lisensi: {m.license or '-'}",
                      (f"Model: {m.model.weights} · task {m.model.task} · "
                       f"imgsz {m.model.imgsz} · conf {m.model.conf}"),
                      "", "Peran zona/garis:"]
            lines += [f"  - {r.role} ({r.kind}) {r.label}" for r in m.regions] or ["  (tidak ada)"]
            lines += ["", "Parameter:"]
            lines += [f"  - {k}: {p.type} = {p.default}  {p.label}" for k, p in m.params.items()] \
                or ["  (tidak ada)"]
            lines += ["", "Metrik:"]
            lines += [f"  - {x.key}: {x.label} [{x.unit or '-'}]"
                      f"{' per zona' if x.per_region else ''}, agg {x.agg}" for x in m.metrics] \
                or ["  (tidak ada)"]
            lines += ["", "Event:"]
            lines += [f"  - {e.type}: {e.label}" for e in m.events] or ["  (tidak ada)"]
        self.detail.setPlainText("\n".join(lines))

    # --------------------------------------------------------------- aksi
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
        self.detail.setPlainText(f"Menguji {info.id}… (memuat model & kode modul)")
        run_async(_quick_check, str(info.path), on_done=self._on_tested,
                  on_failed=lambda msg: self._on_tested((False, msg)))

    def _on_tested(self, result) -> None:
        ok, text = result
        self._testing = False
        self.btn_test.setEnabled(True)
        self.detail.setPlainText(("LULUS" if ok else "GAGAL") + "\n\n" + text +
                                 "\n\nUji penuh dengan video & paritas: "
                                 "python scripts/check_module.py <folder> --video ... --expected ...")
