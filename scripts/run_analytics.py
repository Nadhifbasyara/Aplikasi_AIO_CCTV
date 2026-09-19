import argparse, csv, json, statistics
from pathlib import Path

import cv2
import supervision as sv

from aio_cctv.pipeline import Pipeline
from aio_cctv.sources.file_source import FileSource
from aio_cctv.zones.models import Profile

ap = argparse.ArgumentParser()
ap.add_argument("--profile", required=True)
ap.add_argument("--video")
ap.add_argument("--model", default="models/yolo11s.pt", 
                help="bobot YOLO (default: hasil benchmark Fase 2)")
ap.add_argument("--conf", type=float, default=0.35)
ap.add_argument("--imgsz", type=int, default=640)
ap.add_argument("--out", default="outputs")
ap.add_argument("--show", action="store_true")
args = ap.parse_args()

profile = Profile.load(args.profile)
src = FileSource(args.video or profile.source.uri)
pipe = Pipeline(profile, src.resolution, src.fps, weights=args.model, conf=args.conf,
                imgsz=args.imgsz)
out = Path(args.out) / profile.profile_id
out.mkdir(parents=True, exist_ok=True)

sessions, crossings, occupancy, first = [], [], [], None
frames_total, frames_done = int(src.cap.get(cv2.CAP_PROP_FRAME_COUNT)), 0
info = sv.VideoInfo(width=src.resolution[0], height=src.resolution[1], fps=src.fps)
with sv.VideoSink(str(out / "annotated.mp4"), info) as sink:
    while (f := src.read()) is not None:
        first = first if first is not None else f.image
        frames_done += 1
        res = pipe.step(f.image, f.ts)
        sessions += res.closed_sessions
        crossings += res.crossings
        if f.seq % max(1, round(src.fps)) == 0:
            occupancy.append({"t": round(f.ts, 1), **res.zone_counts})
        vis = pipe.annotate(f.image, res)
        sink.write_frame(vis)
        if args.show:
            cv2.imshow("AIO-CCTV | Analitik", vis)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
sessions += pipe.engine.flush()
src.stop()
if frames_done < frames_total:
    print(f"[peringatan] hanya {frames_done}/{frames_total} frame diproses (dihentikan lebih awal?)")

with open(out / "dwell_sessions.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["zone_id", "track_id", "start_s", "end_s", "duration_s"])
    w.writerows([[s.zone_id, s.track_id, f"{s.start:.2f}", f"{s.end:.2f}", f"{s.duration:.2f}"] for s in sessions])
with open(out / "crossings.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["line_id", "track_id", "direction", "t_s"])
    w.writerows([[c.line_id, c.track_id, c.direction, f"{c.ts:.2f}"] for c in crossings])
with open(out / "occupancy_per_second.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["t"] + [z.id for z in profile.zones])
    w.writeheader(); w.writerows(occupancy)

cv2.imwrite(str(out / "heatmap.png"), pipe.engine.heatmap.render())
cv2.imwrite(str(out / "heatmap_overlay.png"), pipe.engine.heatmap.render(first))

summary = {"model": args.model, "conf": args.conf, "imgsz": args.imgsz,
           "frames": frames_done, "frames_total": frames_total, "zones": {}, "lines": {}}
for z in profile.zones:
    d = [s.duration for s in sessions if s.zone_id == z.id]
    occ = [o[z.id] for o in occupancy]
    summary["zones"][z.name] = {
        "sessions": len(d),
        "dwell_mean_s": round(statistics.mean(d), 1) if d else 0,
        "dwell_median_s": round(statistics.median(d), 1) if d else 0,
        "occupancy_max": max(occ, default=0),
        "occupancy_mean": round(statistics.mean(occ), 2) if occ else 0,
    }
for ln in profile.lines:
    summary["lines"][ln.name] = {"in": sum(c.direction == "in" and c.line_id == ln.id for c in crossings),
                                 "out": sum(c.direction == "out" and c.line_id == ln.id for c in crossings)}
(out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
print(json.dumps(summary, indent=2, ensure_ascii=False))
