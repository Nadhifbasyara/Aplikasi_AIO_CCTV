from dataclasses import dataclass, field

import supervision as sv


@dataclass
class DwellSession:
    zone_id: str
    track_id: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class CrossingEvent:
    line_id: str
    track_id: int
    direction: str      # "in" | "out"
    ts: float


@dataclass
class FrameResult:
    ts: float
    detections: sv.Detections
    zone_counts: dict[str, int] = field(default_factory=dict)
    dwell_now: dict[int, dict[str, float]] = field(default_factory=dict)   # tid -> {zone: detik}
    line_totals: dict[str, tuple[int, int]] = field(default_factory=dict)  # line -> (in, out)
    closed_sessions: list[DwellSession] = field(default_factory=list)
    crossings: list[CrossingEvent] = field(default_factory=list)
