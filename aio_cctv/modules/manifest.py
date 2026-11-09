"""Skema `module.yaml` (Fase 6 §4.2) + pemeriksaan yang tidak butuh memuat kode modul."""
from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from aio_cctv.modules.api import API_VERSION


class ModelSpec(BaseModel):
    weights: str                            # relatif terhadap folder modul
    task: Literal["detect", "pose"] = "detect"
    imgsz: int = 640
    conf: float = Field(0.35, ge=0.0, le=1.0)
    iou: float = Field(0.5, ge=0.0, le=1.0)
    classes: list[str] = Field(default_factory=list)   # nama dari model.names; kosong = semua


class RegionSpec(BaseModel):
    role: str
    kind: Literal["zone", "line"] = "zone"
    label: str = ""
    hint: str = ""
    optional: bool = False
    multiple: bool = False


class ParamSpec(BaseModel):
    type: Literal["float", "int", "bool", "str"] = "float"
    default: Any = None
    min: float | None = None
    max: float | None = None
    label: str = ""

    def coerce(self, value: Any) -> Any:
        """Ubah nilai dari profil ke tipe yang dijanjikan; batas min/max dipaksakan."""
        if self.type == "bool" and isinstance(value, str):   # bool("false") == True
            return value.strip().lower() in ("1", "true", "ya", "yes")
        v = {"float": float, "int": int, "bool": bool, "str": str}[self.type](value)
        if self.type in ("float", "int"):
            if self.min is not None:
                v = max(v, type(v)(self.min))
            if self.max is not None:
                v = min(v, type(v)(self.max))
        return v


class MetricSpec(BaseModel):
    key: str
    label: str = ""
    unit: str = ""
    per_region: bool = False
    agg: Literal["avg", "max", "last", "sum"] = "avg"


class EventSpec(BaseModel):
    type: str
    label: str = ""
    fields: dict[str, str] = Field(default_factory=dict)


class PresetSlot(BaseModel):
    """Satu area yang digambar pengguna di wizard (Fase 8 §4)."""
    key: str
    kind: Literal["zone", "line"] = "zone"
    role: str | None = None                 # harus ada di manifest.regions dengan kind sama
    label: str = ""
    hint: str = ""
    optional: bool = False
    multiple: bool = False


class Preset(BaseModel):
    preset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    label: str
    description: str = ""
    slots: list[PresetSlot] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    rules: list[dict[str, Any]] = Field(default_factory=list)      # format Rule (Fase 7)
    dashboard: list[dict[str, Any]] = Field(default_factory=list)


class ModuleManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    version: str
    api_version: str
    authors: list[str] = Field(default_factory=list)
    license: str = ""
    entrypoint: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*$")
    model: ModelSpec
    requirements: list[str] = Field(default_factory=list)
    regions: list[RegionSpec] = Field(default_factory=list)
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    metrics: list[MetricSpec] = Field(default_factory=list)
    events: list[EventSpec] = Field(default_factory=list)
    dashboard: list[dict[str, Any]] = Field(default_factory=list)   # diformalkan di Fase 7
    path: str = ""                          # diisi loader, bukan oleh pembuat modul

    @field_validator("api_version")
    @classmethod
    def _same_major(cls, v: str) -> str:
        if v.split(".")[0] != API_VERSION.split(".")[0]:
            raise ValueError(f"api_version {v} tidak kompatibel dengan host (v{API_VERSION})")
        return v

    @model_validator(mode="after")
    def _unique_keys(self) -> ModuleManifest:
        for what, keys in (("metrics.key", [m.key for m in self.metrics]),
                           ("events.type", [e.type for e in self.events]),
                           ("regions.role", [r.role for r in self.regions])):
            if dup := {k for k in keys if keys.count(k) > 1}:
                raise ValueError(f"{what} duplikat: {sorted(dup)}")
        return self

    # ------------------------------------------------------------- turunan
    @property
    def dir(self) -> Path:
        return Path(self.path)

    @property
    def weights_path(self) -> Path:
        return self.dir / self.model.weights

    @property
    def metric_keys(self) -> set[str]:
        return {m.key for m in self.metrics}

    @property
    def event_types(self) -> set[str]:
        return {e.type for e in self.events}

    def metric(self, key: str) -> MetricSpec | None:
        return next((m for m in self.metrics if m.key == key), None)

    def resolve_params(self, values: dict | None = None) -> dict:
        """Default manifest ditimpa nilai dari kamera/profil; kunci asing dibuang."""
        values = values or {}
        out = {}
        for key, spec in self.params.items():
            raw = values.get(key, spec.default)
            out[key] = None if raw is None else spec.coerce(raw)
        return out

    def load_presets(self) -> tuple[list[Preset], list[str]]:
        """Preset valid + daftar peringatan. Preset rusak tidak membuat modul tidak valid."""
        from aio_cctv.alerts.rules import Rule

        presets, warnings = [], []
        roles = {(r.role, r.kind) for r in self.regions}
        for f in sorted((self.dir / "presets").glob("*.json")):
            try:
                p = Preset.model_validate(json.loads(f.read_text(encoding="utf-8")))
            except (ValueError, ValidationError) as e:
                warnings.append(f"preset {f.name}: tidak valid ({str(e).splitlines()[0]})")
                continue
            problems = []
            keys = [s.key for s in p.slots]
            if dup := {k for k in keys if keys.count(k) > 1}:
                problems.append(f"slot duplikat {sorted(dup)}")
            problems += [f"peran '{s.role}' ({s.kind}) tidak ada di manifest.regions"
                         for s in p.slots if s.role and (s.role, s.kind) not in roles]
            problems += [f"parameter '{k}' tidak ada di manifest.params"
                         for k in p.params if k not in self.params]
            for r in p.rules:
                try:
                    rule = Rule.model_validate(r)
                except ValidationError as e:
                    problems.append(f"aturan {r.get('id', '?')}: {e.errors()[0]['msg']}")
                    continue
                if rule.metric and rule.metric not in self.metric_keys:
                    problems.append(f"aturan {rule.id}: metrik '{rule.metric}' tidak dikenal")
                if rule.event and rule.event not in self.event_types:
                    problems.append(f"aturan {rule.id}: event '{rule.event}' tidak dikenal")
            for w in p.dashboard:
                if w.get("metric") and w["metric"] not in self.metric_keys:
                    problems.append(f"dashboard: metrik '{w['metric']}' tidak dikenal")
                if w.get("event") and w["event"] not in self.event_types:
                    problems.append(f"dashboard: event '{w['event']}' tidak dikenal")
                elif w.get("field") and w["field"] not in next(
                        (e.fields for e in self.events if e.type == w.get("event")), {}):
                    problems.append(f"dashboard: field '{w['field']}' tidak ada di event "
                                    f"'{w.get('event')}'")
            if problems:
                warnings.append(f"preset {f.name}: " + "; ".join(problems))
            else:
                presets.append(p)
        return presets, warnings

    def check_files(self) -> list[str]:
        errors = []
        if not self.weights_path.exists():
            errors.append(f"bobot model tidak ditemukan: {self.weights_path}")
        file = self.entrypoint.split(":")[0]
        if not (self.dir / f"{file}.py").exists():
            errors.append(f"file entrypoint tidak ditemukan: {file}.py")
        return errors

    def check_requirements(self) -> list[str]:
        errors = []
        for line in self.requirements:
            try:
                req = Requirement(line)
            except InvalidRequirement:
                errors.append(f"requirement tidak valid: {line}")
                continue
            try:
                installed = metadata.version(req.name)
            except metadata.PackageNotFoundError:
                errors.append(f"paket belum terpasang: {req.name}")
                continue
            if req.specifier and not req.specifier.contains(installed, prereleases=True):
                errors.append(f"{req.name} {installed} tidak memenuhi {req.specifier}")
        return errors


def load_manifest(module_dir: str | Path) -> ModuleManifest:
    module_dir = Path(module_dir).resolve()
    data = yaml.safe_load((module_dir / "module.yaml").read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TypeError("module.yaml harus berupa mapping")
    return ModuleManifest.model_validate({**data, "path": str(module_dir)})
