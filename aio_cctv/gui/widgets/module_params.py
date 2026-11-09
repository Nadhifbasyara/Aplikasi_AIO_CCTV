"""Formulir parameter modul yang dibangkitkan dari `manifest.params` (Fase 6 §5.6)."""
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from aio_cctv.modules.manifest import ModuleManifest

BIG = 1e9


class ModuleParamsForm(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.editors: dict[str, tuple[str, QWidget]] = {}

    def set_module(self, manifest: ModuleManifest | None, values: dict | None = None) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.editors.clear()
        if manifest is None or not manifest.params:
            hint = QLabel("Modul ini tidak punya parameter yang bisa diatur."
                          if manifest else "Pilih modul yang valid.")
            hint.setStyleSheet("color:#888;")
            self.form.addRow(hint)
            return
        current = manifest.resolve_params(values)
        for key, spec in manifest.params.items():
            v = current.get(key)
            if spec.type == "bool":
                ed = QCheckBox()
                ed.setChecked(bool(v))
            elif spec.type == "int":
                ed = QSpinBox()
                ed.setRange(int(spec.min if spec.min is not None else -BIG),
                            int(spec.max if spec.max is not None else BIG))
                ed.setValue(int(v or 0))
            elif spec.type == "float":
                ed = QDoubleSpinBox()
                ed.setDecimals(3)
                ed.setRange(spec.min if spec.min is not None else -BIG,
                            spec.max if spec.max is not None else BIG)
                ed.setSingleStep(0.1)
                ed.setValue(float(v or 0.0))
            else:
                ed = QLineEdit("" if v is None else str(v))
            ed.setToolTip(f"{key} (bawaan: {spec.default})")
            self.form.addRow(spec.label or key, ed)
            self.editors[key] = (spec.type, ed)

    def values(self, manifest: ModuleManifest | None) -> dict:
        """Hanya nilai yang berbeda dari default manifest — profil tetap kecil dan modul
        versi baru dengan default baru tetap terpakai."""
        if manifest is None:
            return {}
        out = {}
        for key, (kind, ed) in self.editors.items():
            if kind == "bool":
                v = ed.isChecked()
            elif kind in ("int", "float"):
                v = ed.value()
            else:
                v = ed.text()
            default = manifest.params[key].default
            if not (isinstance(v, float) and default is not None and abs(v - float(default)) < 1e-9) \
                    and v != default:
                out[key] = v
        return out
