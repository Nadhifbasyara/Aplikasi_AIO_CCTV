"""Jalankan satu modul pada kamera RTSP tanpa GUI (Fase 4, diperbarui Fase 6).

Contoh:
  python scripts/run_live.py --profile configs/profiles/kasir.json --url rtsp://... --module baseline
"""
import argparse
import time

import cv2

from aio_cctv.modules.registry import resolve
from aio_cctv.pipeline import Pipeline
from aio_cctv.sources.rtsp_source import RTSPSource
from aio_cctv.zones.models import Profile

ap = argparse.ArgumentParser()
ap.add_argument("--profile", required=True)
ap.add_argument("--url", help="override URL RTSP")
ap.add_argument("--module", default="baseline", help="id modul terpasang atau path folder modul")
args = ap.parse_args()

info = resolve(args.module)
if not info.valid:
    raise SystemExit(f"Modul {info.id} tidak valid: {'; '.join(info.errors)}")
profile = Profile.load(args.profile)
src = RTSPSource(args.url or profile.source.uri, name=profile.profile_id)
if not src.start():
    raise SystemExit("Kamera tidak dapat dihubungi (cek URL/kredensial)")
# zona ternormalisasi -> otomatis cocok resolusi
pipe = Pipeline(info.manifest, profile, src.resolution, src.fps, camera_id=profile.profile_id)

ts = 0.0
while True:
    f = src.read()
    if f is None:
        time.sleep(0.003)
        continue
    ts = f.ts
    res = pipe.step(f.image, f.ts)
    vis = pipe.annotate(f.image, res)
    s = src.stats
    cv2.putText(vis, f"{src.state} | rx {s.recv_fps:.1f}fps | drop {s.dropped} | reconn {s.reconnects}"
                f" | det {pipe.times.detect_ms:.0f}ms mod {pipe.times.module_ms:.0f}ms",
                (10, vis.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.imshow("AIO-CCTV | Live RTSP", vis)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break
pipe.close(ts)
src.stop()
