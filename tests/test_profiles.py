"""Fase 8: profil v2 (migrasi), preset modul, wizard (apply_preset), ekspor/impor .aioprofile."""
import json

import numpy as np
import pytest

from aio_cctv.core.paths import BUNDLED_MODULES_DIR
from aio_cctv.core.profile_io import apply_import, export_profile, import_profile
from aio_cctv.modules.manifest import load_manifest
from aio_cctv.modules.presets import apply_preset, missing_slots
from aio_cctv.modules.registry import inspect
from aio_cctv.zones.models import Profile, SourceConfig, Zone

V1 = {"schema_version": 1, "profile_id": "lama", "vertical": "retail",
      "source": {"type": "file", "uri": "x.mp4"}, "target_classes": ["person"],
      "zones": [{"id": "z1", "name": "Kasir", "type": "dwell",
                 "polygon": [[0, 0], [1, 0], [1, 1]]}]}


@pytest.fixture
def baseline():
    return load_manifest(BUNDLED_MODULES_DIR / "baseline")


def test_v1_profile_migrates_to_v2():
    p = Profile.model_validate(V1)
    assert p.schema_version == 2 and p.module.module_id == "baseline"
    assert p.zones[0].name == "Kasir" and p.rules == [] and p.dashboard == []


def test_baseline_preset_is_valid(baseline):
    presets, warnings = baseline.load_presets()
    assert warnings == [] and [p.preset_id for p in presets] == ["toko-kecil"]


def test_apply_preset_builds_zones_lines_rules_dashboard(baseline):
    preset = baseline.load_presets()[0][0]
    tri = [(0.1, 0.1), (0.4, 0.1), (0.4, 0.4)]
    drawn = {"kasir": [tri], "pintu": [[(0.0, 0.5), (1.0, 0.5)]]}        # "antrean" opsional
    assert missing_slots(preset, {"kasir": [tri]}) == ["Pintu masuk"]
    old = Profile(profile_id="cam1", source=SourceConfig(uri="rtsp://x"),
                  zones=[Zone(id="z9", name="lama", polygon=tri)])
    p = apply_preset(old, baseline, preset, drawn, frame_wh=(640, 360))
    assert [(z.name, z.role) for z in p.zones] == [("Area kasir", "area")]
    assert [(ln.name, ln.role, ln.p1, ln.p2) for ln in p.lines] == \
        [("Pintu masuk", "pintu", (0.0, 0.5), (1.0, 0.5))]
    assert p.module.preset_id == "toko-kecil" and p.module.params == {"grace_s": 2.0,
                                                                      "min_dwell_s": 3.0}
    assert [r.id for r in p.rules] == ["ramai", "offline"] and p.dashboard
    assert p.profile_id == "cam1" and p.source.uri == "rtsp://x" and p.frame_size == (640, 360)
    with pytest.raises(ValueError, match="Pintu masuk"):
        apply_preset(old, baseline, preset, {"kasir": [tri]})


def test_multiple_shapes_in_one_slot_are_numbered(baseline):
    preset = baseline.load_presets()[0][0].model_copy(deep=True)
    preset.slots[0].multiple = True
    tri = [(0.1, 0.1), (0.4, 0.1), (0.4, 0.4)]
    p = apply_preset(Profile(profile_id="c"), baseline, preset,
                     {"kasir": [tri, tri], "pintu": [[(0, 0), (1, 1)]]})
    assert [z.name for z in p.zones] == ["Area kasir 1", "Area kasir 2"]
    assert [z.id for z in p.zones] == ["z1", "z2"]


def test_empty_preset_keeps_zones_and_drops_rules_of_old_module(baseline):
    old = Profile.model_validate({**V1, "module": {"module_id": "modul-lain"},
                                  "rules": [{"id": "a", "type": "metric_gt", "metric": "asing"},
                                            {"id": "b", "type": "metric_gt", "metric": "zone_count"},
                                            {"id": "c", "type": "camera_offline"}],
                                  "dashboard": [{"type": "kpi", "metric": "asing"}]})
    p = apply_preset(old, baseline, None, {}, params={"grace_s": 3})
    assert [z.id for z in p.zones] == ["z1"]                  # zona lama tetap
    assert [r.id for r in p.rules] == ["b", "c"] and p.dashboard == []
    assert p.module.module_id == "baseline" and p.module.params == {"grace_s": 3}


def test_broken_preset_is_warning_not_error(tmp_path):
    import shutil
    mod = tmp_path / "baseline"
    shutil.copytree(BUNDLED_MODULES_DIR / "baseline", mod, ignore=shutil.ignore_patterns("__pycache__"))
    (mod / "module.yaml").write_text((mod / "module.yaml").read_text()
                                     .replace("../../models/", str(BUNDLED_MODULES_DIR.parent / "models") + "/"))
    (mod / "presets" / "rusak.json").write_text(json.dumps({
        "preset_id": "rusak", "label": "Rusak",
        "slots": [{"key": "a", "kind": "zone", "role": "tidak_ada"}],
        "rules": [{"id": "r", "type": "metric_gt", "metric": "asing"}]}))
    info = inspect(mod)
    assert info.errors == [] or all("bobot" in e for e in info.errors)
    assert len(info.warnings) == 1 and "tidak_ada" in info.warnings[0] and "asing" in info.warnings[0]


def test_aioprofile_roundtrip_without_source(tmp_path, baseline):
    preset = baseline.load_presets()[0][0]
    tri = [(0.1, 0.1), (0.4, 0.1), (0.4, 0.4)]
    src = apply_preset(Profile(profile_id="asal", source=SourceConfig(type="rtsp", uri="rtsp://a:b@h")),
                       baseline, preset, {"kasir": [tri], "pintu": [[(0, 0.5), (1, 0.5)]]})
    snap = np.full((36, 64, 3), 127, np.uint8)
    path = export_profile(src, tmp_path / "kasir", snap)
    assert path.suffix == ".aioprofile"
    raw = path.read_bytes()
    assert b"rtsp://" not in raw                               # tanpa URL/kredensial
    imp = import_profile(path)
    assert imp.meta["module"].startswith("baseline@") and imp.snapshot.shape == (36, 64, 3)
    target = Profile(profile_id="tujuan", name="Kamera B", source=SourceConfig(uri="b.mp4"))
    out = apply_import(target, imp.profile)
    assert (out.profile_id, out.name, out.source.uri) == ("tujuan", "Kamera B", "b.mp4")
    assert out.zones == src.zones and out.lines == src.lines and out.rules == src.rules


def test_import_rejects_non_profile_zip(tmp_path):
    import zipfile
    bad = tmp_path / "x.aioprofile"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("lain.txt", "x")
    with pytest.raises(ValueError, match="profile.json"):
        import_profile(bad)
