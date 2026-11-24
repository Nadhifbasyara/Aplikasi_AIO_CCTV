"""Siapkan backend DeepStream (Jalur B, Fase 9 §5.8) untuk satu modul, sebelum dipakai aplikasi.

    python scripts/deepstream_setup.py --module baseline

Langkah: cek Docker + image → bangun image aio-cctv/deepstream:9.1 (sekali) → kompilasi
parser YOLO (sekali) → ekspor bobot modul ke ONNX → bangun engine TensorRT FP16 dengan
menjalankan pipeline sebentar pada klip uji. Semua hasil disimpan di cache
(~/.cache/aio-cctv/deepstream), jadi kamera pertama di aplikasi langsung jalan.
Tanpa skrip ini pun aplikasi menyiapkannya otomatis, hanya kamera pertama menunggu ±1–2 menit.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aio_cctv.inference.deepstream import service
from aio_cctv.modules.registry import resolve


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--module", default="baseline")
    ap.add_argument("--video", default="data/bench/klip720p15.mp4",
                    help="klip untuk membangun engine (file, diputar berulang)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    info = resolve(args.module)
    if info is None or not info.valid:
        sys.exit(f"modul {args.module!r} tidak ditemukan/tidak valid")
    m = info.manifest
    print("1/4 cek Docker & image…", flush=True)
    service.check_environment()
    print("2/4 parser YOLO…", flush=True)
    print("    ", service.parser_lib())
    print("3/4 ekspor ONNX…", flush=True)
    d, names = service.ensure_onnx(m)
    print(f"     {d}  ({len(names)} kelas)")
    engine = d / f"model_b{service.ENGINE_BATCH}_gpu0_fp16.engine"
    if engine.exists():
        print("4/4 engine TensorRT sudah ada:", engine)
        return 0
    print("4/4 membangun engine TensorRT FP16 (±30–60 detik)…", flush=True)
    video = (ROOT / args.video).resolve()
    if not video.exists():
        sys.exit(f"klip tidak ditemukan: {video}")
    _group, slot = service.POOL.acquire(("setup", m.id), m, "setup", video.as_uri(), 1280, 720)
    ok = service.wait_streaming(slot, 300)
    time.sleep(1)
    service.POOL.release("setup")
    if not ok or not engine.exists():
        sys.exit("gagal — lihat log di ~/.cache/aio-cctv/deepstream/logs/")
    print("selesai:", engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
