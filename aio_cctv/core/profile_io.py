"""Ekspor/impor profil kamera sebagai `.aioprofile` (ZIP) — Fase 8 §6.

Isi: profile.json (tanpa sumber/URL/kredensial), meta.json, dan snapshot.jpg opsional
sebagai acuan visual saat zona dipasang di kamera lain.
"""
from __future__ import annotations

import json
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from aio_cctv import __version__ as APP_VERSION
from aio_cctv.zones.models import Profile

FORMAT = "aioprofile/1"


@dataclass
class ImportedProfile:
    profile: Profile
    meta: dict
    snapshot: np.ndarray | None


def export_profile(profile: Profile, path: str | Path, snapshot: np.ndarray | None = None) -> Path:
    path = Path(path)
    if path.suffix != ".aioprofile":
        path = path.with_suffix(".aioprofile")
    data = profile.model_dump(mode="json", exclude={"source"})
    meta = {"format": FORMAT, "app_version": APP_VERSION, "exported_at": time.time(),
            "module": f"{profile.module.module_id}@{profile.module.module_version}",
            "frame_size": list(profile.frame_size) if profile.frame_size else None,
            "zones": len(profile.zones), "lines": len(profile.lines), "rules": len(profile.rules)}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("profile.json", json.dumps(data, indent=2, ensure_ascii=False))
        z.writestr("meta.json", json.dumps(meta, indent=2))
        if snapshot is not None:
            ok, buf = cv2.imencode(".jpg", snapshot, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                z.writestr("snapshot.jpg", buf.tobytes())
    return path


def import_profile(path: str | Path) -> ImportedProfile:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "profile.json" not in names:
            raise ValueError("bukan berkas .aioprofile (profile.json tidak ada)")
        meta = json.loads(z.read("meta.json")) if "meta.json" in names else {}
        if meta.get("format", FORMAT).split("/")[0] != FORMAT.split("/")[0]:
            raise ValueError(f"format tidak dikenal: {meta.get('format')}")
        profile = Profile.model_validate(json.loads(z.read("profile.json")))
        snap = None
        if "snapshot.jpg" in names:
            snap = cv2.imdecode(np.frombuffer(z.read("snapshot.jpg"), np.uint8), cv2.IMREAD_COLOR)
    return ImportedProfile(profile, meta, snap)


def apply_import(target: Profile, imported: Profile) -> Profile:
    """Pasang isi profil impor ke profil kamera tujuan: id, nama, & sumber tujuan tetap."""
    out = imported.model_copy(deep=True)
    out.profile_id, out.name, out.source = target.profile_id, target.name, target.source
    return out
