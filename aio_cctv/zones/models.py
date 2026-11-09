"""Model konfigurasi kamera: zona/garis + modul + aturan (Proposal §8.4; skema v2, Fase 8)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from aio_cctv.alerts.rules import Rule

SCHEMA_VERSION = 2
DEFAULT_MODULE = "baseline"

Point = tuple[float, float]          # (x, y) ternormalisasi 0..1


def _check_normalized(points: list[Point]) -> list[Point]:
    for x, y in points:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"titik {x, y} harus ternormalisasi 0..1")
    return points


class Zone(BaseModel):
    id: str
    name: str
    type: Literal["dwell", "occupancy", "presence"] = "dwell"
    polygon: list[Point]
    color: str = "#E53935"
    role: str | None = None                       # peran dari manifest modul (Fase 6)

    @field_validator("polygon")
    @classmethod
    def _valid_polygon(cls, v: list[Point]) -> list[Point]:
        if len(v) < 3:
            raise ValueError("poligon minimal 3 titik")
        return _check_normalized(v)


class Line(BaseModel):
    id: str
    name: str
    type: Literal["counting"] = "counting"
    p1: Point
    p2: Point
    color: str = "#FDD835"
    role: str | None = None

    @field_validator("p1", "p2")
    @classmethod
    def _valid_point(cls, v: Point) -> Point:
        return _check_normalized([v])[0]


class SourceConfig(BaseModel):
    type: Literal["file", "rtsp", "webcam"] = "file"
    uri: str


class ModuleBinding(BaseModel):
    """Modul analitik kamera ini (Fase 8; sebelumnya di CameraConfig)."""
    module_id: str = DEFAULT_MODULE
    module_version: str = ""                      # versi saat profil dibuat/diubah (info)
    preset_id: str | None = None                  # preset wizard yang dipakai, bila ada
    params: dict[str, Any] = Field(default_factory=dict)   # hanya yang berbeda dari default


class Profile(BaseModel):
    """Satu profil per kamera. v1 (Fase 1–7) dimigrasi otomatis saat dibaca."""
    schema_version: int = SCHEMA_VERSION
    profile_id: str
    name: str = ""
    vertical: str = "generic"                     # warisan v1, tidak dipakai host
    source: SourceConfig | None = None            # info saja; tidak ikut ekspor .aioprofile
    frame_size: tuple[int, int] | None = None     # resolusi saat zona digambar (info saja)
    target_classes: list[str] = Field(default_factory=list)   # warisan v1; kelas dari manifest
    module: ModuleBinding = Field(default_factory=ModuleBinding)
    zones: list[Zone] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)         # kosong = aturan bawaan host
    dashboard: list[dict[str, Any]] = Field(default_factory=list)  # kosong = manifest.dashboard

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data: Any) -> Any:
        """v1 -> v2: zona/garis tetap; modul diisi baseline (CameraManager memindahkan pilihan
        modul & aturan lama dari cameras.json, lihat camera_manager.migrate_camera)."""
        if isinstance(data, dict) and data.get("schema_version", 1) < SCHEMA_VERSION:
            data = {**data, "schema_version": SCHEMA_VERSION}
            data.setdefault("module", {"module_id": DEFAULT_MODULE})
        return data

    @property
    def region_names(self) -> dict[str, str]:
        return {z.id: z.name for z in self.zones} | {ln.id: ln.name for ln in self.lines}

    @classmethod
    def load(cls, path: str | Path) -> Profile:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")


def to_pixels(points: list[Point], w: int, h: int) -> np.ndarray:
    """Ubah titik ternormalisasi ke piksel (int32, siap untuk OpenCV/Supervision)."""
    return np.array([[round(x * (w - 1)), round(y * (h - 1))] for x, y in points], dtype=np.int32)


def to_normalized(points: list[tuple[int, int]], w: int, h: int) -> list[Point]:
    return [(round(x / (w - 1), 4), round(y / (h - 1), 4)) for x, y in points]