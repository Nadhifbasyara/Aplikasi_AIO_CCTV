"""Unduh video referensi YouTube (Proposal §2) sebagai bahan uji lokal.

Catatan: hanya untuk riset/pengujian lokal. Jangan didistribusikan ulang.
"""
import argparse
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import download_range_func

REFERENSI = {
    "ref1": "https://youtu.be/KMJS66jBtVQ?si=PbM_dBs4SHp24Xup",
}


def download(url: str, out_dir: Path, name: str, max_height: int = 720,
             start: float | None = None, end: float | None = None) -> None:
    opts = {
        "format": f"bv*[height<={max_height}][ext=mp4]/b[height<={max_height}][ext=mp4]/b",
        "outtmpl": str(out_dir / f"{name}.%(ext)s"),
        "noplaylist": True,
    }
    if start is not None and end is not None:
        opts["download_ranges"] = download_range_func(None, [(start, end)])
        opts["force_keyframes_at_cuts"] = True
    with YoutubeDL(opts) as ydl:
        ydl.download([url])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="URL YouTube lain (opsional)")
    ap.add_argument("--name", default="custom")
    ap.add_argument("--out", default="data/videos")
    ap.add_argument("--max-height", type=int, default=720)
    ap.add_argument("--start", type=float, help="detik awal potongan")
    ap.add_argument("--end", type=float, help="detik akhir potongan")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    targets = {args.name: args.url} if args.url else REFERENSI
    for name, url in targets.items():
        print(f"[unduh] {name} <- {url}")
        download(url, out, name, args.max_height, args.start, args.end)


if __name__ == "__main__":
    main()