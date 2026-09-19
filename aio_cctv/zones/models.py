"""Model konfigurasi zona/garis (evolusi JSON Proposal §8.4, skema v1)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator

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

    @field_validator("p1", "p2")
    @classmethod
    def _valid_point(cls, v: Point) -> Point:
        return _check_normalized([v])[0]


class SourceConfig(BaseModel):
    type: Literal["file", "rtsp", "webcam"] = "file"
    uri: str


class Profile(BaseModel):
    schema_version: int = 1
    profile_id: str
    vertical: str = "generic"                     # fnb, retail, clinic, ... (Proposal §1)
    source: SourceConfig
    frame_size: tuple[int, int] | None = None     # resolusi saat zona digambar (info saja)
    target_classes: list[str] = Field(default_factory=lambda: ["person"])
    zones: list[Zone] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)

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