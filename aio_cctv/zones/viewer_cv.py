import argparse

import cv2

from aio_cctv.core.colors import hex_to_bgr
from aio_cctv.zones.models import Profile, to_pixels


def draw_profile(frame, profile: Profile):
    h, w = frame.shape[:2]
    for z in profile.zones:
        cv2.polylines(frame, [to_pixels(z.polygon, w, h)], True, hex_to_bgr(z.color), 2)
    for ln in profile.lines:
        p1, p2 = to_pixels([ln.p1, ln.p2], w, h)
        cv2.line(frame, tuple(map(int, p1)), tuple(map(int, p2)), hex_to_bgr(ln.color), 3)
    return frame


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--video", help="override sumber di profil")
    args = ap.parse_args()
    profile = Profile.load(args.profile)
    cap = cv2.VideoCapture(args.video or profile.source.uri)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        cv2.imshow("AIO-CCTV | Viewer", draw_profile(frame, profile))
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
