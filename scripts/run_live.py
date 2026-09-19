import argparse
import time

import cv2

from aio_cctv.pipeline import Pipeline
from aio_cctv.sources.rtsp_source import RTSPSource
from aio_cctv.zones.models import Profile

ap = argparse.ArgumentParser()
ap.add_argument("--profile", required=True)
ap.add_argument("--url", help="override URL RTSP")
args = ap.parse_args()

profile = Profile.load(args.profile)
src = RTSPSource(args.url or profile.source.uri, name=profile.profile_id)
if not src.start():
    raise SystemExit("Kamera tidak dapat dihubungi (cek URL/kredensial)")
pipe = Pipeline(profile, src.resolution, src.fps)   # zona ternormalisasi -> otomatis cocok resolusi

while True:
    f = src.read()
    if f is None:
        time.sleep(0.003)
        continue
    res = pipe.step(f.image, f.ts)
    vis = pipe.annotate(f.image, res)
    s = src.stats
    cv2.putText(vis, f"{src.state} | rx {s.recv_fps:.1f}fps | drop {s.dropped} | reconn {s.reconnects}",
                (10, vis.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.imshow("AIO-CCTV | Live RTSP", vis)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break
src.stop()
