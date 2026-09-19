import json
import subprocess


def probe(url: str, timeout_s: int = 10) -> dict:
    """Ambil info stream via ffprobe: codec, resolusi, fps, profil."""
    cmd = ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
           "-show_entries", "stream=codec_name,profile,width,height,avg_frame_rate,r_frame_rate",
           "-of", "json", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    if out.returncode != 0:
        return {"ok": False, "error": out.stderr.strip()[-300:]}   # mis. 401 Unauthorized, 404
    s = json.loads(out.stdout)["streams"][0]
    num, den = map(int, s.get("avg_frame_rate", "0/1").split("/"))
    return {"ok": True, "codec": s["codec_name"], "profile": s.get("profile"),
            "width": s["width"], "height": s["height"], "fps": round(num / den, 2) if den else None}
