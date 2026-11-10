"""Benchmark kapasitas kamera per PC (Fase 9 §2–§3.3).

Menjalankan N `PipelineWorker` — kode yang sama dengan aplikasi, tanpa jendela — terhadap N
stream RTSP simulasi (MediaMTX + ffmpeg `-c copy`), untuk N = 1, 2, 4, …, lalu mencatat
FPS per kamera, latensi, dan pemakaian CPU/GPU/VRAM/RAM. Berhenti setelah N pertama yang gagal
kriteria QoS; N terbesar yang lolos = N_max.

Contoh (cepat, ±10 menit):
  python scripts/benchmark_capacity.py --label "0 baseline" --cams 1,2,4,6,8,12,16 \\
      --warmup 20 --duration 60 --out docs/hasil-evaluasi/kapasitas.csv
Untuk angka final Bab IV pakai --warmup 60 --duration 600 (Fase 9 §3.3).

Pengaturan aplikasi bisa ditimpa dengan --set kunci=nilai (mis. --set inference=shared
--set max_batch=8 --set target_fps=10). Kunci yang tidak dikenal worker diabaikan.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from aio_cctv.core.sysmon import SystemSampler  # noqa: E402
from aio_cctv.gui.utils import DEFAULTS  # noqa: E402
from aio_cctv.gui.workers.pipeline_worker import PipelineWorker  # noqa: E402
from aio_cctv.modules.registry import resolve  # noqa: E402
from aio_cctv.zones.models import Profile  # noqa: E402

FIELDS = ["waktu", "label", "n", "lolos", "alasan", "fps_min", "fps_median", "fps_target",
          "latensi_p50_ms", "latensi_p95_ms", "drop_pct", "cpu_pct", "gpu_pct", "vram_mb",
          "vram_pct", "ram_mb", "proc_cpu_pct", "decode_ms", "detect_ms", "module_ms", "render_ms", "pengaturan"]


def _parse_value(v: str):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return {"true": True, "false": False}.get(v.lower(), v)


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def pctl(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def start_sim(video: str, n: int) -> subprocess.Popen:
    p = subprocess.Popen([str(ROOT / "scripts/sim_cameras.sh"), video, str(n)],
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    time.sleep(3)                               # beri waktu ffmpeg mem-publish semua stream
    return p


def stop_sim(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    subprocess.run([str(ROOT / "scripts/sim_cameras.sh"), "stop"], cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_n(n: int, args, module, profile, settings, sampler, vram_total) -> dict:
    stats: dict[str, list[dict]] = {}
    errors: list[str] = []
    measuring = [False]

    def on_stats(cam_id: str, s: dict) -> None:
        if measuring[0]:
            stats.setdefault(cam_id, []).append(s)

    workers = []
    for i in range(1, n + 1):
        w = PipelineWorker(f"bench{i}", f"rtsp://127.0.0.1:8554/cam{i + args.cam_offset}",
                           profile, settings,
                           module, module_id=module.id)
        w.gui_visible = args.render == "visible"
        w.stats_ready.connect(on_stats)
        w.error.connect(lambda cid, msg: errors.append(f"{cid}: {msg}"))
        workers.append(w)
    for w in workers:
        w.start()

    wait(args.warmup * 1000)
    measuring[0] = True
    sys_samples = []
    proc_cpu = []                               # % satu core; 100 = satu core penuh
    sampler.proc.cpu_percent(None)
    t_end = time.monotonic() + args.duration
    while time.monotonic() < t_end:
        wait(1000)
        sys_samples.append(sampler.sample())
        proc_cpu.append(sampler.proc.cpu_percent(None))
    measuring[0] = False
    for w in workers:
        w.stop()

    # per kamera: rata-rata FPS proses & p95 latensi selama jendela ukur
    fps_per_cam, lat_p95, lat_all, drop = [], [], [], []
    stage = {k: [] for k in ("decode_ms", "detect_ms", "module_ms", "render_ms")}
    target = []
    for i in range(1, n + 1):
        rows = stats.get(f"bench{i}", [])
        if not rows:
            fps_per_cam.append(0.0)
            lat_p95.append(float("inf"))
            continue
        fps_per_cam.append(statistics.mean(r["proc_fps"] for r in rows))
        p95s = [r.get("latency_p95_ms", r["latency_ms"]) for r in rows]
        lat_p95.append(pctl(p95s, 0.95))
        lat_all += [r["latency_ms"] for r in rows]
        rx = rows[-1]["total_frames"] - rows[0]["total_frames"]
        cx = rows[-1]["decoded_frames"] - rows[0]["decoded_frames"]
        drop.append(100 * (1 - cx / rx) if rx > 0 else 0.0)   # hanya selama jendela ukur
        target.append(rows[-1].get("target_fps") or rows[-1].get("stream_fps", 0.0))
        for k in stage:
            stage[k].append(statistics.mean(r.get(k, 0.0) for r in rows))

    def avg(key):
        vals = [s[key] for s in sys_samples if s.get(key) is not None]
        return statistics.mean(vals) if vals else 0.0

    vram = max((s["vram_mb"] or 0) for s in sys_samples) if sys_samples else 0
    out = {
        "n": n, "fps_min": min(fps_per_cam), "fps_median": statistics.median(fps_per_cam),
        "fps_target": statistics.median(target) if target else 0.0,
        "latensi_p50_ms": pctl(lat_all, 0.5), "latensi_p95_ms": max(lat_p95),
        "drop_pct": statistics.mean(drop) if drop else 0.0,
        "cpu_pct": avg("cpu_pct"), "gpu_pct": avg("gpu_pct"), "vram_mb": vram,
        "vram_pct": 100 * vram / vram_total if vram_total else 0.0,
        "ram_mb": max((s["ram_mb"] for s in sys_samples), default=0),
        "proc_cpu_pct": statistics.mean(proc_cpu) if proc_cpu else 0.0,
        **{k: statistics.mean(v) if v else 0.0 for k, v in stage.items()},
    }
    # Kriteria QoS Fase 9 §2. Drop karena stride/FPS target yang disengaja tidak dihitung
    # gagal; yang dijaga adalah FPS minimum tiap kamera & latensi.
    reasons = []
    if out["fps_min"] < args.f_min:
        reasons.append(f"FPS min {out['fps_min']:.1f} < {args.f_min}")
    if out["latensi_p95_ms"] > args.max_latency:
        reasons.append(f"latensi p95 {out['latensi_p95_ms']:.0f} ms > {args.max_latency}")
    if out["cpu_pct"] >= 90:
        reasons.append(f"CPU {out['cpu_pct']:.0f}% ≥ 90%")
    if out["vram_pct"] >= 90:
        reasons.append(f"VRAM {out['vram_pct']:.0f}% ≥ 90%")
    if errors:
        reasons.append(f"error: {errors[0]}")
    out["lolos"] = not reasons
    out["alasan"] = "; ".join(reasons)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--label", default="baseline", help="nama konfigurasi (baris tabel ablation)")
    ap.add_argument("--module", default="baseline")
    ap.add_argument("--profile", default="configs/profiles/demo_ref1.json")
    ap.add_argument("--video", default="data/bench/klip720p15.mp4")
    ap.add_argument("--cams", default="1,2,4,6,8,12,16")
    ap.add_argument("--warmup", type=int, default=60)
    ap.add_argument("--duration", type=int, default=600)
    ap.add_argument("--f-min", type=float, default=8.0)
    ap.add_argument("--max-latency", type=float, default=500.0)
    ap.add_argument("--set", action="append", default=[], metavar="KUNCI=NILAI")
    ap.add_argument("--render", choices=["visible", "hidden"], default="visible",
                    help="visible = semua tile tampil di Live (terberat); hidden = analisis saja")
    ap.add_argument("--cam-offset", type=int, default=0,
                    help="pakai stream cam<offset+1>… (untuk beberapa proses benchmark sekaligus)")
    ap.add_argument("--no-sim", action="store_true", help="simulasi sudah dijalankan terpisah")
    ap.add_argument("--keep-going", action="store_true", help="lanjut walau sudah gagal")
    ap.add_argument("--out", default="docs/hasil-evaluasi/kapasitas.csv")
    args = ap.parse_args()

    overrides = dict(kv.split("=", 1) for kv in args.set)
    settings = {**DEFAULTS, "device": "auto", "blur": False,
                **{k: _parse_value(v) for k, v in overrides.items()}}
    module = resolve(args.module)
    if module is None or not module.valid:
        sys.exit(f"modul {args.module!r} tidak ditemukan/tidak valid")
    profile = Profile.model_validate_json(Path(args.profile).read_text())
    cams = [int(c) for c in args.cams.split(",")]

    app = QApplication(sys.argv)  # noqa: F841  (QImage & sinyal lintas-thread butuh app)
    sampler = SystemSampler()
    vram_total = 0
    if sampler._gpu is not None:
        vram_total = sampler._nvml.nvmlDeviceGetMemoryInfo(sampler._gpu).total / 2**20

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    sim = None if args.no_sim else start_sim(args.video, max(cams) + args.cam_offset)
    results = []
    try:
        with open(out, "a", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=FIELDS)
            if new:
                wr.writeheader()
            for n in cams:
                print(f"[{args.label}] N={n}: pemanasan {args.warmup}s, ukur {args.duration}s…",
                      flush=True)
                r = run_n(n, args, module, profile, settings, sampler, vram_total)
                r.update(waktu=datetime.now().isoformat(timespec="seconds"), label=args.label,
                         pengaturan=json.dumps({**overrides, "render": args.render}))
                wr.writerow({k: (f"{v:.2f}" if isinstance(v, float) else v)
                             for k, v in r.items() if k in FIELDS})
                fh.flush()
                results.append(r)
                print(f"   {'LOLOS' if r['lolos'] else 'GAGAL'}  fps min/med "
                      f"{r['fps_min']:.1f}/{r['fps_median']:.1f}  p95 {r['latensi_p95_ms']:.0f} ms"
                      f"  CPU {r['cpu_pct']:.0f}%  GPU {r['gpu_pct']:.0f}%  VRAM "
                      f"{r['vram_mb']:.0f} MB  proses {r['proc_cpu_pct']:.0f}%  det {r['detect_ms']:.1f} mod {r['module_ms']:.1f}"
                      f" dec {r['decode_ms']:.1f} ren {r['render_ms']:.1f} ms  {r['alasan']}",
                      flush=True)
                if not r["lolos"] and not args.keep_going:
                    break
    finally:
        if sim is not None:
            stop_sim(sim)
    passed = [r["n"] for r in results if r["lolos"]]
    n_max = max(passed) if passed else 0
    print(f"[{args.label}] N_max = {n_max}  ({platform.node()}, hasil di {out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
