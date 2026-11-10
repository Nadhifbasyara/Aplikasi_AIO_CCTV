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


def test_error_messages_never_carry_camera_passwords():
    """Pesan error PyAV/ffprobe memuat URL lengkap; semua jalur tampil/log wajib menyamarkan."""
    from aio_cctv.gui.utils import friendly_error
    from aio_cctv.sources.url_builder import mask
    raw = "Server returned 400 Bad Request: 'rtsp://ceits:R4has1a%21@10.0.0.1:8554/live'"
    assert "R4has1a" not in mask(raw) and "rtsp://ceits:****@10.0.0.1" in mask(raw)
    assert "R4has1a" not in friendly_error(raw)


def test_dvr_channel_urls_and_masking():
    from aio_cctv.gui.camera_manager import CameraConfig, build_source_uri
    from aio_cctv.sources.url_builder import build_url, has_channels, mask

    assert has_channels("hikvision") and has_channels("dahua") and not has_channels("tapo")
    assert build_url("hikvision", "192.0.2.10", "u", "p", "main", channel=12).endswith(
        "/Streaming/Channels/1201")
    assert build_url("hikvision", "192.0.2.10", "u", "p", "sub", channel=3).endswith(
        "/Streaming/Channels/302")
    assert "channel=4&subtype=1" in build_url("dahua", "192.0.2.10", "u", "p", "sub", channel=4)
    assert "channel=2&stream=0" in build_url("xmeye", "192.0.2.10", "u", "rahasia", "main",
                                             channel=2)
    assert build_url("tapo", "192.0.2.10", "u", "p", "sub", channel=5).endswith("/stream2")
    cam = CameraConfig("uji-dvr", "DVR CH2", "dahua", host="192.0.2.10", user="admin", channel=2)
    uri = build_source_uri(cam, "rahasia")
    assert "channel=2" in uri and "rahasia" not in mask(uri)
    # cameras.json lama tanpa kunci channel/device tetap terbaca
    assert CameraConfig("uji-lama", "Lama", "hikvision").channel == 1
