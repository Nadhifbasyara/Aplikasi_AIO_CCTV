"""Benchmark sederhana yolo11n/s/m pada satu video (bahan pemilihan model, Proposal §7 & §10)."""
import sys
import time

import numpy as np
import supervision as sv

from aio_cctv.inference.detector import Detector

video = sys.argv[1]
frames = [f for _, f in zip(range(300), sv.get_video_frames_generator(video))]  # 300 frame pertama

print(f"{'model':<12}{'imgsz':>6}{'ms/frame':>10}{'FPS':>7}{'avg_det':>9}")
for weights in ["models/yolo11n.pt", "models/yolo11s.pt", "models/yolo11m.pt"]:
    for imgsz in (640, 480):
        d = Detector(weights, imgsz=imgsz)
        d.warmup()
        times, counts = [], []
        for f in frames:
            t0 = time.perf_counter()
            counts.append(len(d(f)))
            times.append(time.perf_counter() - t0)
        ms = 1000 * np.mean(times)
        print(f"{weights.split('/')[-1]:<12}{imgsz:>6}{ms:>10.1f}{1000 / ms:>7.1f}{np.mean(counts):>9.2f}")
