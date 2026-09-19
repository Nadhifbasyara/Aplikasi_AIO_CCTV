"""Editor marker area berbasis OpenCV (Fase 1).

Kontrol:
  Klik kiri  : tambah titik
  Enter      : tutup poligon (mode zona)
  L          : ganti mode ZONA <-> GARIS
  U          : undo titik terakhir
  D          : hapus zona/garis terakhir
  S          : simpan JSON
  Q / Esc    : keluar
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

from aio_cctv.core.colors import PALETTE, hex_to_bgr
from aio_cctv.zones.models import Line, Profile, SourceConfig, Zone, to_normalized, to_pixels

WIN = "AIO-CCTV | Editor Zona"
HELP = "Klik=titik  Enter=tutup zona  L=mode  U=undo  D=hapus  S=simpan  Q=keluar"


def _pt(a) -> tuple[int, int]:
    return int(a[0]), int(a[1])


class ZoneEditor:
    def __init__(self, frame: np.ndarray, profile: Profile):
        self.base = frame
        self.h, self.w = frame.shape[:2]
        self.profile = profile
        self.points: list[tuple[int, int]] = []
        self.mode = "zona"
        self.dirty = False

    # ---------- interaksi ----------
    def on_mouse(self, event, x, y, flags, param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((x, y))
            if self.mode == "garis" and len(self.points) == 2:
                self.commit()

    def commit(self) -> None:
        if self.mode == "zona" and len(self.points) >= 3:
            n = len(self.profile.zones) + 1
            self.profile.zones.append(Zone(
                id=f"z{n}", name=f"Zona {n}",
                polygon=to_normalized(self.points, self.w, self.h),
                color=PALETTE[(n - 1) % len(PALETTE)],
            ))
            self.dirty = True
        elif self.mode == "garis" and len(self.points) == 2:
            n = len(self.profile.lines) + 1
            p1, p2 = to_normalized(self.points, self.w, self.h)
            self.profile.lines.append(Line(id=f"l{n}", name=f"Garis {n}", p1=p1, p2=p2))
            self.dirty = True
        self.points = []

    def undo(self) -> None:
        if self.points:
            self.points.pop()

    def delete_last(self) -> None:
        items = self.profile.zones if self.mode == "zona" else self.profile.lines
        if items:
            items.pop()
            self.dirty = True

    # ---------- tampilan ----------
    def render(self) -> np.ndarray:
        img = self.base.copy()
        overlay = img.copy()
        for z in self.profile.zones:
            pts = to_pixels(z.polygon, self.w, self.h)
            cv2.fillPoly(overlay, [pts], hex_to_bgr(z.color))
        img = cv2.addWeighted(overlay, 0.25, img, 0.75, 0)

        for z in self.profile.zones:
            pts = to_pixels(z.polygon, self.w, self.h)
            cv2.polylines(img, [pts], True, hex_to_bgr(z.color), 2)
            cv2.putText(img, f"{z.name} [{z.type}]", _pt(pts[0]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        for ln in self.profile.lines:
            p1, p2 = to_pixels([ln.p1, ln.p2], self.w, self.h)
            cv2.line(img, _pt(p1), _pt(p2), hex_to_bgr(ln.color), 3)
            cv2.putText(img, ln.name, _pt(p1), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        for p in self.points:                              # bentuk yang sedang digambar
            cv2.circle(img, p, 5, (0, 255, 0), -1)
        if len(self.points) > 1:
            cv2.polylines(img, [np.array(self.points, np.int32)], False, (0, 255, 0), 1)

        status = f"MODE: {self.mode.upper()} | zona={len(self.profile.zones)} garis={len(self.profile.lines)}"
        status += " *belum disimpan*" if self.dirty else ""
        # teks bantuan di bawah frame agar tidak menimpa timestamp bawaan CCTV (kiri atas)
        lines = [status, HELP]
        top = self.h - 12 - 22 * len(lines)
        band = img.copy()
        cv2.rectangle(band, (0, top), (self.w, self.h), (0, 0, 0), -1)
        img = cv2.addWeighted(band, 0.5, img, 0.5, 0)
        for i, text in enumerate(lines):
            # tanpa outline hitam: di OpenCV 5 lebar huruf berubah sesuai thickness sehingga
            # outline tidak sejajar; latar gelap sudah cukup menjaga keterbacaan
            cv2.putText(img, text, (10, top + 22 * (i + 1)), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (255, 255, 255), 1, cv2.LINE_AA)
        return img


def grab_frame(uri: str, at_sec: float) -> np.ndarray:
    cap = cv2.VideoCapture(uri)
    cap.set(cv2.CAP_PROP_POS_MSEC, at_sec * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"Gagal membaca frame dari {uri} pada detik {at_sec}")
    return frame


def main() -> None:
    ap = argparse.ArgumentParser(description="Editor marker area (zona & garis)")
    ap.add_argument("video", help="path video (atau URL RTSP, dipakai mulai Fase 4)")
    ap.add_argument("--profile", required=True, help="path JSON profil (dibuat jika belum ada)")
    ap.add_argument("--time", type=float, default=0.0, help="detik frame acuan")
    ap.add_argument("--vertical", default="generic")
    args = ap.parse_args()

    frame = grab_frame(args.video, args.time)
    h, w = frame.shape[:2]
    path = Path(args.profile)
    if path.exists():
        profile = Profile.load(path)
    else:
        src_type = "rtsp" if args.video.startswith("rtsp://") else "file"
        profile = Profile(profile_id=path.stem, vertical=args.vertical,
                          source=SourceConfig(type=src_type, uri=args.video))
    profile.frame_size = (w, h)

    editor = ZoneEditor(frame, profile)
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WIN, editor.on_mouse)

    while True:
        cv2.imshow(WIN, editor.render())
        key = cv2.waitKey(20) & 0xFF
        if key in (13, 10):
            editor.commit()
        elif key == ord("l"):
            editor.points, editor.mode = [], ("garis" if editor.mode == "zona" else "zona")
        elif key == ord("u"):
            editor.undo()
        elif key == ord("d"):
            editor.delete_last()
        elif key == ord("s"):
            profile.save(path)
            editor.dirty = False
            print(f"[simpan] {path}")
        elif key in (ord("q"), 27):
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
