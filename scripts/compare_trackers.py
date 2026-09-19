"""Bandingkan ByteTrack vs BoT-SORT pada satu video (Fase 3 §5, bahan Fase 9).

Contoh:
  python scripts/compare_trackers.py data/videos/ref1.mp4 --frames 300
"""
import argparse
import time

import numpy as np
import supervision as sv

from aio_cctv.inference.detector import Detector
from aio_cctv.tracking.tracker import ByteTrackTracker, UltralyticsTracker


def summarize(name: str, per_frame: list[sv.Detections], times: list[float]) -> None:
    ids = [set(d.tracker_id.tolist()) if d.tracker_id is not None else set() for d in per_frame]
    unique = set().union(*ids)
    lifetimes = {}                                   # tid -> jumlah frame muncul
    for s in ids:
        for t in s:
            lifetimes[t] = lifetimes.get(t, 0) + 1
    print(f"{name:<10}{1000 * np.mean(times):>9.1f}{1 / np.mean(times):>8.1f}"
          f"{np.mean([len(s) for s in ids]):>13.2f}{len(unique):>10}"
          f"{np.median(list(lifetimes.values())) if lifetimes else 0:>14.0f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--model", default="models/yolo11s.pt")
    args = ap.parse_args()

    info = sv.VideoInfo.from_video_path(args.video)
    frames = [f for _, f in zip(range(args.frames), sv.get_video_frames_generator(args.video))]
    print(f"{len(frames)} frame, {info.fps:.2f} fps, model {args.model}\n")
    print(f"{'tracker':<10}{'ms/frame':>9}{'FPS':>8}{'track/frame':>13}{'ID unik':>10}"
          f"{'umur median':>14}")

    # ByteTrack: detektor + tracker terpisah (dipakai Pipeline)
    det, bt = Detector(args.model), ByteTrackTracker(info.fps)
    det.warmup()
    out, times = [], []
    for f in frames:
        t0 = time.perf_counter()
        out.append(bt.update(det(f)))
        times.append(time.perf_counter() - t0)
    summarize("ByteTrack", out, times)

    # BoT-SORT: deteksi + tracking sekaligus
    bs = UltralyticsTracker(args.model)
    bs.detect_and_track(frames[0])                   # warmup
    bs.reset()
    out, times = [], []
    for f in frames:
        t0 = time.perf_counter()
        out.append(bs.detect_and_track(f))
        times.append(time.perf_counter() - t0)
    summarize("BoT-SORT", out, times)

    print("\nCatatan: 'ID unik' lebih kecil (mendekati jumlah orang sebenarnya) dan 'umur median'"
          "\nlebih panjang = ID lebih stabil (lebih sedikit ID switch). Ukuran pasti: MOTA/IDF1 Fase 9.")


if __name__ == "__main__":
    main()
