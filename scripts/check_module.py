"""Uji kepatuhan paket modul sebelum serah terima (Fase 6 §6).

Contoh:
  python scripts/check_module.py modules/baseline --video data/videos/ref1_360p.mp4
  python scripts/check_module.py modules/ta1-x --profile p.json --expected samples/expected.jsonl
  python scripts/check_module.py modules/baseline --video v.mp4 --record acuan.jsonl
"""
import argparse
import sys

from aio_cctv.modules.check import run_checks
from aio_cctv.zones.models import Profile

ap = argparse.ArgumentParser()
ap.add_argument("module_dir")
ap.add_argument("--video", help="default: file video pertama di <modul>/samples/")
ap.add_argument("--frames", type=int, default=300, help="0 = semua frame")
ap.add_argument("--profile", help="profil zona/garis; default: profil sintetis dari manifest")
ap.add_argument("--expected", help="JSONL acuan dari pembuat modul untuk uji paritas")
ap.add_argument("--record", help="tulis keluaran JSONL (untuk membuat acuan)")
ap.add_argument("--device", default=None)
args = ap.parse_args()

ok = run_checks(args.module_dir, video=args.video, frames=args.frames,
                profile=Profile.load(args.profile) if args.profile else None,
                expected=args.expected, device=args.device, record_to=args.record)
sys.exit(0 if ok else 1)
