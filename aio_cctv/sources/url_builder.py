import json
import re
from pathlib import Path
from urllib.parse import quote

# path relatif terhadap lokasi file ini (bukan folder kerja), agar tetap jalan dari GUI/folder lain
BRANDS_FILE = Path(__file__).resolve().parents[2] / "configs" / "camera_brands.json"
BRANDS = json.loads(BRANDS_FILE.read_text(encoding="utf-8"))


def build_url(brand: str, host: str, user: str = "", password: str = "",
              stream: str = "main", port: int | None = None, url: str = "") -> str:
    b = BRANDS[brand]
    return b[stream].format(
        host=host, port=port or b.get("port", 554), url=url,
        user=quote(user, safe=""), password=quote(password, safe=""),   # '@', ':' dll aman
    )


_CRED = re.compile(r"(rtsp://[^:/@]+:)([^@]+)(@)")
_XM = re.compile(r"(password=)([^&]+)")


def mask(url: str) -> str:
    """Sembunyikan password sebelum ditulis ke log/UI."""
    return _XM.sub(r"\1****", _CRED.sub(r"\1****\3", url))
