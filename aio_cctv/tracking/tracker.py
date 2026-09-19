import supervision as sv


class ByteTrackTracker:
    """ByteTrack (Zhang et al., 2022) via Supervision — pilihan utama Proposal §7."""

    def __init__(self, fps: float, lost_track_seconds: float = 2.0,
                 activation_threshold: float = 0.25, matching_threshold: float = 0.8):
        self.fps = fps
        self._t = sv.ByteTrack(
            track_activation_threshold=activation_threshold,
            lost_track_buffer=int(round(fps * lost_track_seconds)),
            minimum_matching_threshold=matching_threshold,
            frame_rate=int(round(fps)),
        )

    def update(self, det: sv.Detections) -> sv.Detections:
        return self._t.update_with_detections(det)

    def reset(self) -> None:
        self._t.reset()


class UltralyticsTracker:
    """BoT-SORT (Aharon et al., 2022) bawaan Ultralytics — pembanding ByteTrack (Proposal §7).

    Berbeda dengan ByteTrackTracker, kelas ini melakukan deteksi + tracking sekaligus:
    model.track(persist=True, tracker='botsort.yaml').
    """

    def __init__(self, weights: str = "models/yolo11s.pt", tracker_cfg: str = "botsort.yaml",
                 classes=(0,), conf: float = 0.35, imgsz: int = 640):
        from ultralytics import YOLO

        self.model = YOLO(weights)
        self.cfg, self.classes, self.conf, self.imgsz = tracker_cfg, list(classes), conf, imgsz

    def detect_and_track(self, frame) -> sv.Detections:
        r = self.model.track(frame, persist=True, tracker=self.cfg, classes=self.classes,
                             conf=self.conf, imgsz=self.imgsz, verbose=False)[0]
        return sv.Detections.from_ultralytics(r)

    def reset(self) -> None:
        predictor = getattr(self.model, "predictor", None)
        for t in getattr(predictor, "trackers", None) or []:
            t.reset()
