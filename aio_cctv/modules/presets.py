"""Menerapkan preset modul ke profil kamera (Fase 8 §4–5). Dipakai wizard; tanpa Qt."""
from __future__ import annotations

from aio_cctv.alerts.rules import Rule
from aio_cctv.core.colors import PALETTE
from aio_cctv.modules.manifest import ModuleManifest, Preset
from aio_cctv.zones.models import Line, ModuleBinding, Profile, Zone

Point = tuple[float, float]


def missing_slots(preset: Preset, drawn: dict[str, list[list[Point]]]) -> list[str]:
    """Label slot wajib yang belum digambar."""
    return [s.label or s.key for s in preset.slots if not s.optional and not drawn.get(s.key)]


def apply_preset(profile: Profile, manifest: ModuleManifest, preset: Preset | None,
                 drawn: dict[str, list[list[Point]]], params: dict | None = None,
                 frame_wh: tuple[int, int] | None = None) -> Profile:
    """Profil baru: zona/garis dari bentuk yang digambar per slot (koordinat 0..1), modul,
    parameter, aturan, dan dashboard dari preset. Sumber & id profil dipertahankan.

    preset=None ("Kosong"): hanya modul & parameter yang diganti, zona/garis lama tetap.
    """
    out = profile.model_copy(deep=True)
    if profile.module.module_id != manifest.id:
        # modul berganti: dashboard & aturan yang merujuk metrik/event modul lama tidak berlaku
        out.dashboard = []
        out.rules = [r for r in out.rules
                     if (not r.metric or r.metric in manifest.metric_keys)
                     and (not r.event or r.event in manifest.event_types)]
    out.module = ModuleBinding(module_id=manifest.id, module_version=manifest.version,
                               preset_id=preset.preset_id if preset else None,
                               params=dict(params if params is not None
                                           else preset.params if preset else {}))
    if frame_wh:
        out.frame_size = tuple(frame_wh)
    if preset is None:
        return out
    if missing := missing_slots(preset, drawn):
        raise ValueError(f"Area wajib belum digambar: {', '.join(missing)}")

    zones, lines = [], []
    for slot in preset.slots:
        shapes = drawn.get(slot.key) or []
        for n, pts in enumerate(shapes, 1):
            name = (slot.label or slot.key) + (f" {n}" if len(shapes) > 1 else "")
            color = PALETTE[(len(zones) + len(lines)) % len(PALETTE)]
            if slot.kind == "zone":
                zones.append(Zone(id=f"z{len(zones) + 1}", name=name, polygon=pts,
                                  role=slot.role, color=color))
            else:
                lines.append(Line(id=f"l{len(lines) + 1}", name=name, p1=pts[0], p2=pts[1],
                                  role=slot.role, color=color))
    out.zones, out.lines = zones, lines
    out.rules = [Rule.model_validate(r) for r in preset.rules]
    out.dashboard = [dict(w) for w in preset.dashboard]
    return out
