"""Jalankan satu modul analitik pada file video (pengganti run_analytics.py, Fase 6).

Keluaran di <out>/<profil>/<modul>/:
  records.jsonl   satu baris per frame (format uji paritas, Fase 6 §6)
  events.csv      semua event modul
  summary.json    ringkasan metrik (sesuai `agg` manifest) + jumlah event per tipe
  annotated.mp4   video beranotasi (--no-video untuk melewati)
  heatmap*.png    bila modul mengisi ModuleResult.heat

Contoh:
  python scripts/run_module.py --module baseline --profile configs/profiles/demo_ref1.json \
      --video data/videos/ref1_360p.mp4
"""
import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import supervision as sv

from aio_cctv.annotate import render_heat
from aio_cctv.modules.host import result_to_record
from aio_cctv.modules.registry import resolve
from aio_cctv.pipeline import Pipeline
from aio_cctv.sources.file_source import FileSource
from aio_cctv.zones.models import Profile

ap = argparse.ArgumentParser()
ap.add_argument("--module", default="baseline", help="id modul terpasang atau path folder modul")
ap.add_argument("--profile", required=True)
ap.add_argument("--video", help="default: source.uri di profil")
ap.add_argument("--frames", type=int, default=0, help="batasi jumlah frame (0 = semua)")
ap.add_argument("--device", default=None)
ap.add_argument("--out", default="outputs")
ap.add_argument("--no-video", action="store_true")
ap.add_argument("--show", action="store_true")
args = ap.parse_args()

info = resolve(args.module)
if not info.valid:
    sys.exit(f"Modul {info.id} tidak valid:\n  - " + "\n  - ".join(info.errors))
m = info.manifest
profile = Profile.load(args.profile)
src = FileSource(args.video or profile.source.uri)
pipe = Pipeline(m, profile, src.resolution, src.fps, camera_id=profile.profile_id,
                device=args.device)
out = Path(args.out) / profile.profile_id / m.id
out.mkdir(parents=True, exist_ok=True)

samples: dict[tuple[str, str | None], list[float]] = defaultdict(list)
events, first, last_heat, ts, n = [], None, None, 0.0, 0
info_v = sv.VideoInfo(width=src.resolution[0], height=src.resolution[1], fps=src.fps)
sink = None if args.no_video else sv.VideoSink(str(out / "annotated.mp4"), info_v).__enter__()
with open(out / "records.jsonl", "w", encoding="utf-8") as rec:
    while (f := src.read()) is not None and not (args.frames and n >= args.frames):
        first = first if first is not None else f.image
        n, ts = n + 1, f.ts
        res = pipe.step(f.image, f.ts)
        rec.write(json.dumps(result_to_record(f.seq, f.ts, res), ensure_ascii=False) + "\n")
        for mt in res.metrics:
            samples[(mt.key, mt.region_id)].append(mt.value)
        events += res.events
        last_heat = res.heat if res.heat is not None else last_heat
        if sink is not None or args.show:
            vis = pipe.annotate(f.image, res)
            if sink is not None:
                sink.write_frame(vis)
            if args.show:
                cv2.imshow(f"AIO-CCTV | {m.name}", vis)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    closing = pipe.close(ts)
    events += closing
    if closing:
        rec.write(json.dumps(result_to_record(-1, ts, None, closing), ensure_ascii=False) + "\n")
if sink is not None:
    sink.__exit__(None, None, None)
src.stop()

with open(out / "events.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["type", "ts", "region_id", "track_id", "data"])
    w.writerows([[e.type, f"{e.ts:.3f}", e.region_id or "", "" if e.track_id is None else e.track_id,
                  json.dumps(e.data, ensure_ascii=False)] for e in events])

if last_heat is not None:
    cv2.imwrite(str(out / "heatmap.png"), render_heat(last_heat, src.resolution))
    cv2.imwrite(str(out / "heatmap_overlay.png"), render_heat(last_heat, src.resolution, first))

AGG = {"avg": statistics.mean, "max": max, "last": lambda xs: xs[-1], "sum": sum}
names = {z.id: z.name for z in profile.zones} | {ln.id: ln.name for ln in profile.lines}
summary = {"module": f"{m.id}@{m.version}", "weights": m.model.weights, "frames": n,
           "metrics": {}, "events": dict(Counter(e.type for e in events))}
for (key, region), xs in sorted(samples.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
    spec = m.metric(key)
    agg = spec.agg if spec else "avg"
    label = f"{key}@{names.get(region, region)}" if region else key
    summary["metrics"][label] = {agg: round(AGG[agg](xs), 3), "max": max(xs)}
(out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
print(json.dumps(summary, indent=2, ensure_ascii=False))
print(f"Keluaran: {out}")
