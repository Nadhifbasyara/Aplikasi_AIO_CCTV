from dataclasses import dataclass


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
