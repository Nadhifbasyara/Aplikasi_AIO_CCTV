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
    ap.add_argument("--batches", default="4,8,16,32",
                    help="ukuran batch engine yang disiapkan (aplikasi memilih sesuai jumlah "
                         "kamera per model)")
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
    video = (ROOT / args.video).resolve()
    if not video.exists():
        sys.exit(f"klip tidak ditemukan: {video}")
    batches = [int(b) for b in args.batches.split(",")]
    for i, batch in enumerate(batches, 1):
        engine = d / f"model_b{batch}_gpu0_fp16.engine"
        if engine.exists():
            print(f"4/4 [{i}/{len(batches)}] engine batch {batch} sudah ada")
            continue
        print(f"4/4 [{i}/{len(batches)}] membangun engine batch {batch} (±30–60 detik)…",
              flush=True)
        # jumlah sumber = batch → service memilih engine ukuran ini
        ids = [f"setup{k}" for k in range(batch)]
        slot = None
        for cid in ids:
            _g, s = service.POOL.acquire(("setup", m.id), m, cid, video.as_uri(), 1280, 720)
            slot = slot or s
        ok = service.wait_streaming(slot, 300)
        time.sleep(1)
        for cid in ids:
            service.POOL.release(cid)
        if not ok or not engine.exists():
            sys.exit("gagal — lihat log di ~/.cache/aio-cctv/deepstream/logs/")
    print("selesai:", d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
