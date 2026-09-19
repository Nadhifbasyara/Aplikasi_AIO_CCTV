"""MVP Fase 2: deteksi orang + hitung per zona pada video (mis. unduhan YouTube).

Contoh:
  python scripts/detect_zones.py --profile configs/profiles/demo_ref1.json --show
"""
import argparse
import csv
import json
import time
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

from aio_cctv.inference.detector import Detector
from aio_cctv.zones.models import Profile, to_pixels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--video", help="override sumber di profil")
    ap.add_argument("--model", default="models/yolo11n.pt")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    profile = Profile.load(args.profile)
    video = args.video or profile.source.uri
    info = sv.VideoInfo.from_video_path(video)
    w, h = info.resolution_wh
    out_dir = Path(args.out) / profile.profile_id
    out_dir.mkdir(parents=True, exist_ok=True)

    detector = Detector(args.model, profile.target_classes, args.conf, imgsz=args.imgsz)
    detector.warmup()

    # Zona -> PolygonZone (Supervision, Proposal §7 "Analitik zona")
    zones = []
    for z in profile.zones:
        pz = sv.PolygonZone(polygon=to_pixels(z.polygon, w, h),
                            triggering_anchors=(sv.Position.BOTTOM_CENTER,))
        ann = sv.PolygonZoneAnnotator(zone=pz, color=sv.Color.from_hex(z.color),
                                      thickness=2, text_scale=0.7)
        zones.append((z, pz, ann))

    box_ann = sv.BoxAnnotator(thickness=2)
    label_ann = sv.LabelAnnotator(text_scale=0.5)

    csv_f = open(out_dir / "counts_per_second.csv", "w", newline="")
    writer = csv.writer(csv_f)
    writer.writerow(["second", "total_person"] + [z.name for z in profile.zones])

    max_count = {z.name: 0 for z in profile.zones}
    infer_times: list[float] = []
    fps = info.fps or 25

    with sv.VideoSink(str(out_dir / "annotated.mp4"), info) as sink:
        for idx, frame in enumerate(sv.get_video_frames_generator(video)):
            t0 = time.perf_counter()
            det = detector(frame)
            infer_times.append(time.perf_counter() - t0)

            counts = {}
            for z, pz, _ in zones:
                counts[z.name] = int(pz.trigger(det).sum())
                max_count[z.name] = max(max_count[z.name], counts[z.name])

            labels = [f"person {c:.2f}" for c in det.confidence]
            vis = box_ann.annotate(frame.copy(), det)
            vis = label_ann.annotate(vis, det, labels=labels)
            for _, _, ann in zones:
                vis = ann.annotate(vis)          # menampilkan current_count di tengah zona
            # ringkasan di bawah frame agar tidak menimpa timestamp bawaan CCTV (kiri atas)
            band = vis.copy()
            cv2.rectangle(band, (0, h - 40), (w, h), (0, 0, 0), -1)
            vis = cv2.addWeighted(band, 0.55, vis, 0.45, 0)
            text = f"Total orang: {len(det)}  |  " + "  |  ".join(f"{k}: {v}" for k, v in counts.items())
            cv2.putText(vis, text, (10, h - 13), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 2, cv2.LINE_AA)
            sink.write_frame(vis)

            if idx % int(round(fps)) == 0:     # sampel 1x per detik video
                writer.writerow([idx // int(round(fps)), len(det)] + list(counts.values()))

            if args.show:
                cv2.imshow("AIO-CCTV | Deteksi", vis)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    csv_f.close()
    cv2.destroyAllWindows()
    summary = {
        "video": video, "model": args.model, "imgsz": args.imgsz, "frames": len(infer_times),
        "mean_infer_ms": round(1000 * float(np.mean(infer_times)), 2),
        "fps_infer": round(1 / float(np.mean(infer_times)), 1),
        "max_person_per_zone": max_count,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
