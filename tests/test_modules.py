"""Kontrak host–modul (Fase 6): manifest, registry, host. Tanpa GPU/video — modul uji sintetis."""
import io
import zipfile

import numpy as np
import pytest
import supervision as sv

from aio_cctv.core.paths import BUNDLED_MODULES_DIR
from aio_cctv.modules import registry
from aio_cctv.modules.api import Event, Metric, ModuleResult
from aio_cctv.modules.host import ModuleError, ModuleRunner, build_context, validate_result
from aio_cctv.modules.manifest import load_manifest
from aio_cctv.zones.models import Line, Profile, SourceConfig, Zone

MANIFEST = """\
id: {id}
name: Modul uji
version: 0.1.0
api_version: "{api}"
entrypoint: analyzer:Mod
model: {{weights: w.pt, classes: [person]}}
regions: [{{role: kasir, kind: zone}}]
params:
  ambang: {{type: float, default: 2.5, min: 0, max: 10}}
  aktif:  {{type: bool, default: true}}
  meledak: {{type: bool, default: false}}
metrics: [{{key: hitung, label: Hitung, per_region: true}}]
events: [{{type: ping}}]
"""

ANALYZER = """\
import supervision as sv
from aio_cctv.modules.api import AnalyticsModule, Event, Metric, ModuleResult
from .helper import TAG

class Mod(AnalyticsModule):
    def process(self, frame, detections, ts):
        if self.ctx.params.get("meledak"):
            raise RuntimeError("sengaja")
        frame[0, 0] = 1          # melanggar kontrak: frame read-only
        return ModuleResult(sv.Detections.empty())

    def close(self, ts):
        return [Event("ping", ts, data={"tag": TAG})]
"""


def make_module(root, mid="uji", api="1.0", tag="A", analyzer=ANALYZER):
    d = root / mid
    d.mkdir(parents=True)
    (d / "module.yaml").write_text(MANIFEST.format(id=mid, api=api))
    (d / "analyzer.py").write_text(analyzer)
    (d / "helper.py").write_text(f"TAG = {tag!r}\n")
    (d / "w.pt").write_bytes(b"")              # cukup ada; tes ini tidak memuat model
    return d


def profile():
    return Profile(profile_id="p", source=SourceConfig(uri=""),
                   zones=[Zone(id="z1", name="Kasir", role="kasir",
                               polygon=[(0, 0), (1, 0), (1, 1)])],
                   lines=[Line(id="l1", name="Pintu", p1=(0, 0.5), p2=(1, 0.5))])


# ------------------------------------------------------------------ manifest
def test_baseline_manifest_is_valid():
    m = load_manifest(BUNDLED_MODULES_DIR / "baseline")
    assert m.id == "baseline" and m.model.classes == ["person"]
    assert {"people_count", "zone_count", "line_in", "line_out"} <= m.metric_keys


def test_incompatible_api_major_is_rejected(tmp_path):
    info = registry.inspect(make_module(tmp_path, api="2.0"))
    assert not info.valid and any("api_version" in e for e in info.errors)


def test_missing_weights_reported(tmp_path):
    d = make_module(tmp_path)
    (d / "w.pt").unlink()
    info = registry.inspect(d)
    assert not info.valid and any("bobot" in e for e in info.errors)


def test_params_defaults_coercion_and_bounds(tmp_path):
    m = load_manifest(make_module(tmp_path))
    assert m.resolve_params() == {"ambang": 2.5, "aktif": True, "meledak": False}
    assert m.resolve_params({"ambang": "99", "aktif": "false", "asing": 1}) == \
        {"ambang": 10.0, "aktif": False, "meledak": False}


# ------------------------------------------------------------------ registry
def test_two_modules_with_same_file_names_do_not_clash(tmp_path):
    a = load_manifest(make_module(tmp_path, "mod-a", tag="A"))
    b = load_manifest(make_module(tmp_path, "mod-b", tag="B"))
    ca, cb = registry.load_entrypoint(a), registry.load_entrypoint(b)
    assert ca is not cb
    assert ca(None).close(0)[0].data["tag"] == "A"
    assert cb(None).close(0)[0].data["tag"] == "B"


def test_install_from_zip_and_refuse_duplicate(tmp_path):
    src = make_module(tmp_path / "src", "dari-zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in src.iterdir():
            z.write(f, f"paket/{f.name}")       # ZIP dengan satu folder induk
    zpath = tmp_path / "m.zip"
    zpath.write_bytes(buf.getvalue())
    dest = tmp_path / "terpasang"
    dest.mkdir()
    info = registry.install(zpath, dest_root=dest)
    assert info.valid and info.path == (dest / "dari-zip").resolve()
    with pytest.raises(FileExistsError):
        registry.install(zpath, dest_root=dest)
    assert registry.install(zpath, replace=True, dest_root=dest).valid


def test_zip_slip_is_rejected(tmp_path):
    zpath = tmp_path / "jahat.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("../../keluar.txt", "x")
    with pytest.raises(ValueError, match="tidak aman"):
        registry.install(zpath, dest_root=tmp_path)


# ------------------------------------------------------------------ host
def test_context_carries_regions_roles_and_params(tmp_path):
    m = load_manifest(make_module(tmp_path))
    ctx = build_context(m, profile(), "cam1", (640, 360), 12.5, {"ambang": 4})
    assert [(r.id, r.kind, r.role) for r in ctx.regions] == \
        [("z1", "zone", "kasir"), ("l1", "line", None)]
    assert ctx.params["ambang"] == 4.0 and ctx.fps == 12.5
    assert ctx.to_pixels([(1.0, 1.0)]).tolist() == [[639, 359]]


def test_frame_is_read_only_for_modules(tmp_path):
    m = load_manifest(make_module(tmp_path))
    runner = ModuleRunner(m, build_context(m, profile(), "c", (8, 8), 10))
    with pytest.raises(ModuleError, match="read-only"):
        runner.process(np.zeros((8, 8, 3), np.uint8), sv.Detections.empty(), 0.0)


def test_module_exception_is_wrapped_with_module_id(tmp_path):
    m = load_manifest(make_module(tmp_path))
    runner = ModuleRunner(m, build_context(m, profile(), "c", (8, 8), 10, {"meledak": True}))
    with pytest.raises(ModuleError) as e:
        runner.process(np.zeros((8, 8, 3), np.uint8), sv.Detections.empty(), 0.0)
    assert e.value.module_id == "uji" and "sengaja" in str(e.value)


def test_validate_result_flags_contract_violations(tmp_path):
    m = load_manifest(make_module(tmp_path))
    res = ModuleResult(sv.Detections.empty(),
                       metrics=[Metric("hitung", 1, "z1"), Metric("asing", 1)],
                       events=[Event("ping", 0, data={"img": np.zeros(3)}),
                               Event("pong", 0)])
    problems = validate_result(m, res)
    assert any("'asing'" in p for p in problems)
    assert any("'pong'" in p for p in problems)
    assert any("JSON" in p for p in problems)
    assert validate_result(m, ModuleResult(sv.Detections.empty(),
                                           metrics=[Metric("hitung", 3, "z1")])) == []
