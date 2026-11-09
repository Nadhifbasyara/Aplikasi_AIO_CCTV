"""Menjalankan satu instans modul untuk satu kamera (Fase 6 §5): konteks, timing, isolasi error."""
from __future__ import annotations

import json
import time

import numpy as np
import supervision as sv

from aio_cctv.modules.api import CameraContext, Event, ModuleResult, Region
from aio_cctv.modules.manifest import ModuleManifest
from aio_cctv.modules.registry import load_entrypoint
from aio_cctv.zones.models import Profile

MAX_EVENT_DATA = 2048        # byte JSON; mencegah modul menyelipkan citra/embedding (Fase 7 §3.7)


class ModuleError(RuntimeError):
    """Pengecualian dari kode modul. Hanya kamera yang memakai modul itu yang berhenti."""

    def __init__(self, module_id: str, stage: str, cause: BaseException):
        super().__init__(f"Modul '{module_id}' gagal di {stage}: {type(cause).__name__}: {cause}")
        self.module_id, self.stage, self.cause = module_id, stage, cause


def regions_from_profile(profile: Profile) -> list[Region]:
    regions = [Region(z.id, z.name, "zone", list(z.polygon), z.role) for z in profile.zones]
    regions += [Region(ln.id, ln.name, "line", [ln.p1, ln.p2], ln.role) for ln in profile.lines]
    return regions


def build_context(manifest: ModuleManifest, profile: Profile, camera_id: str,
                  frame_wh: tuple[int, int], fps: float, params: dict | None = None) -> CameraContext:
    return CameraContext(camera_id=camera_id, frame_wh=tuple(frame_wh), fps=float(fps),
                         regions=regions_from_profile(profile),
                         params=manifest.resolve_params(params), module_dir=manifest.path)


def validate_result(manifest: ModuleManifest, res, events: list[Event] | None = None) -> list[str]:
    """Pelanggaran kontrak pada satu hasil. Dipakai check_module dan mode strict."""
    problems = []
    if res is not None:
        if not isinstance(res, ModuleResult):
            return [f"process() mengembalikan {type(res).__name__}, bukan ModuleResult"]
        if not isinstance(res.tracks, sv.Detections):
            problems.append("ModuleResult.tracks bukan sv.Detections")
        for m in res.metrics:
            if m.key not in manifest.metric_keys:
                problems.append(f"metrik '{m.key}' tidak terdaftar di manifest")
            if not isinstance(m.value, (int, float, np.integer, np.floating)):
                problems.append(f"nilai metrik '{m.key}' bukan angka")
        if res.heat is not None and not isinstance(res.heat, np.ndarray):
            problems.append("ModuleResult.heat bukan ndarray")
        events = list(res.events) + list(events or [])
    for e in events or []:
        if e.type not in manifest.event_types:
            problems.append(f"event '{e.type}' tidak terdaftar di manifest")
        try:
            if len(json.dumps(e.data)) > MAX_EVENT_DATA:
                problems.append(f"Event.data '{e.type}' lebih dari {MAX_EVENT_DATA} byte")
        except TypeError:
            problems.append(f"Event.data '{e.type}' tidak bisa diserialisasi JSON")
    return problems


class ModuleRunner:
    def __init__(self, manifest: ModuleManifest, ctx: CameraContext, strict: bool = False):
        self.manifest, self.ctx, self.strict = manifest, ctx, strict
        self.module_ms = 0.0
        try:
            cls = load_entrypoint(manifest)
            self.instance = cls(ctx)
        except Exception as e:
            raise ModuleError(manifest.id, "inisialisasi", e) from e

    def process(self, frame: np.ndarray, det: sv.Detections, ts: float) -> ModuleResult:
        frame.flags.writeable = False          # overlay digambar host, bukan modul
        t0 = time.perf_counter()
        try:
            res = self.instance.process(frame, det, ts)
        except Exception as e:
            raise ModuleError(self.manifest.id, "process()", e) from e
        finally:
            self.module_ms = (time.perf_counter() - t0) * 1e3
        if self.strict and (problems := validate_result(self.manifest, res)):
            raise ModuleError(self.manifest.id, "validasi hasil", ValueError("; ".join(problems)))
        return res

    def close(self, ts: float) -> list[Event]:
        try:
            events = list(self.instance.close(ts) or [])
        except Exception as e:
            raise ModuleError(self.manifest.id, "close()", e) from e
        if self.strict and (problems := validate_result(self.manifest, None, events)):
            raise ModuleError(self.manifest.id, "validasi close()", ValueError("; ".join(problems)))
        return events


def result_to_record(frame: int, ts: float, res: ModuleResult | None,
                     events: list[Event] | None = None) -> dict:
    """Satu baris JSONL: format keluaran acuan untuk uji paritas (Fase 6 §6)."""
    rec: dict = {"frame": frame, "ts": round(float(ts), 6), "tracks": [], "metrics": [],
                 "events": []}
    evs = list(events or [])
    if res is not None:
        d = res.tracks
        if len(d):
            tids = d.tracker_id if d.tracker_id is not None else [None] * len(d)
            cls = d.class_id if d.class_id is not None else [None] * len(d)
            rec["tracks"] = [[None if t is None else int(t), *[round(float(v), 2) for v in box],
                              None if c is None else int(c)]
                             for t, box, c in zip(tids, d.xyxy, cls)]
        rec["metrics"] = [[m.key, m.region_id, round(float(m.value), 6)] for m in res.metrics]
        evs = list(res.events) + evs
    rec["events"] = [{"type": e.type, "ts": round(float(e.ts), 6), "region_id": e.region_id,
                      "track_id": e.track_id, "data": e.data} for e in evs]
    return rec
