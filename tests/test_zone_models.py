import pytest
from pydantic import ValidationError

from aio_cctv.zones.models import Profile, SourceConfig, Zone, to_normalized, to_pixels


def test_roundtrip(tmp_path):
    p = Profile(profile_id="t", source=SourceConfig(uri="x.mp4"),
                zones=[Zone(id="z1", name="A", polygon=[(0, 0), (1, 0), (1, 1)])])
    f = tmp_path / "p.json"
    p.save(f)
    assert Profile.load(f) == p


def test_polygon_min_points():
    with pytest.raises(ValidationError):
        Zone(id="z", name="x", polygon=[(0, 0), (1, 1)])


def test_out_of_range():
    with pytest.raises(ValidationError):
        Zone(id="z", name="x", polygon=[(0, 0), (1.2, 0), (1, 1)])


def test_pixel_conversion_is_resolution_independent():
    norm = to_normalized([(0, 0), (1279, 719)], 1280, 720)
    assert to_pixels(norm, 640, 360).tolist() == [[0, 0], [639, 359]]
