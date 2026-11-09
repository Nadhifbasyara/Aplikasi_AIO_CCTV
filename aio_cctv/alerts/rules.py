"""Rule engine generik (Fase 7 §3.5). Aturan merujuk key metrik/event dari manifest modul.

Dua kelompok aturan:
- aturan DATA (metric_gt, metric_lt, event) dievaluasi di thread worker tiap frame;
- aturan HOST (camera_offline, module_error, overload) dievaluasi CameraManager.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

DATA_TYPES = ("metric_gt", "metric_lt", "event")
HOST_TYPES = ("camera_offline", "module_error", "overload")
TYPE_LABELS = {
    "metric_gt": "Metrik di atas ambang",
    "metric_lt": "Metrik di bawah ambang",
    "event": "Event terjadi",
    "camera_offline": "Kamera offline",
    "module_error": "Modul error",
    "overload": "FPS proses di bawah ambang",
}


class Rule(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["metric_gt", "metric_lt", "event", "camera_offline", "module_error", "overload"]
    metric: str | None = None
    event: str | None = None
    region_role: str | None = None          # None = semua zona (atau metrik tingkat kamera)
    threshold: float | None = None
    for_s: float = Field(0, ge=0)           # kondisi harus bertahan selama ini
    window_s: float = Field(60, gt=0)       # khusus "event"
    min_count: int = Field(1, ge=1)         # khusus "event"
    cooldown_s: float = Field(300, ge=0)
    severity: Literal["info", "warning", "critical"] = "warning"
    message: str = ""
    enabled: bool = True

    def describe(self) -> str:
        what = self.metric or self.event or ""
        where = f" @{self.region_role}" if self.region_role else ""
        thr = f" {'>' if self.type == 'metric_gt' else '<'} {self.threshold:g}" \
            if self.threshold is not None and self.type in ("metric_gt", "metric_lt", "overload") else ""
        detail = f"{what}{where}{thr}".strip()
        return f"{TYPE_LABELS[self.type]}: {detail}" if detail else TYPE_LABELS[self.type]


def default_rules() -> list[Rule]:
    """Aturan host yang aktif untuk kamera yang belum diatur sendiri."""
    return [Rule(id="offline", type="camera_offline", for_s=60, cooldown_s=600, severity="critical",
                 message="Kamera tidak mengirim gambar lebih dari 1 menit"),
            Rule(id="modul", type="module_error", cooldown_s=600, severity="critical",
                 message="Modul analitik berhenti karena error")]


def parse_rules(raw: list[dict] | None) -> list[Rule]:
    return [Rule.model_validate(r) for r in raw] if raw else default_rules()


@dataclass
class Alert:
    rule_id: str
    message: str
    ts: float                                # domain waktu yang sama dengan masukan evaluate()
    severity: str = "warning"


class RuleEngine:
    def __init__(self, rules: list[Rule]):
        self.rules = [r for r in rules if r.enabled]
        self._since: dict[str, float] = {}       # kapan kondisi mulai terpenuhi
        self._last_fired: dict[str, float] = {}
        self._recent: dict[str, list[float]] = {}

    @property
    def data_rules(self) -> list[Rule]:
        return [r for r in self.rules if r.type in DATA_TYPES]

    def _gate(self, r: Rule, cond: bool, ts: float) -> bool:
        if not cond:
            self._since.pop(r.id, None)
            return False
        if ts - self._since.setdefault(r.id, ts) < r.for_s:
            return False
        if ts - self._last_fired.get(r.id, -1e18) < r.cooldown_s:
            return False
        self._last_fired[r.id] = ts
        return True

    def _alert(self, r: Rule, ts: float, detail: str = "") -> Alert:
        msg = r.message or r.describe()
        return Alert(r.id, f"{msg} ({detail})" if detail else msg, ts, r.severity)

    def evaluate(self, res, ts: float, region_of_role: dict[str, set[str]]) -> list[Alert]:
        """Aturan DATA terhadap satu ModuleResult."""
        out = []
        for r in self.data_rules:
            detail = ""
            if r.type in ("metric_gt", "metric_lt"):
                regions = region_of_role.get(r.region_role, set()) if r.region_role else None
                vals = [m.value for m in res.metrics if m.key == r.metric
                        and (regions is None or m.region_id in regions)]
                if not vals or r.threshold is None:
                    cond = False
                elif r.type == "metric_gt":
                    v = max(vals)
                    cond, detail = v > r.threshold, f"nilai {v:g}"
                else:
                    v = min(vals)
                    cond, detail = v < r.threshold, f"nilai {v:g}"
            else:                                    # "event"
                regions = region_of_role.get(r.region_role, set()) if r.region_role else None
                hist = self._recent.setdefault(r.id, [])
                hist += [e.ts for e in res.events if e.type == r.event
                         and (regions is None or e.region_id in regions)]
                hist[:] = [t for t in hist if ts - t <= r.window_s]
                cond, detail = len(hist) >= r.min_count, f"{len(hist)}× dalam {r.window_s:g} s"
            if self._gate(r, cond, ts):
                out.append(self._alert(r, ts, detail))
        return out

    def evaluate_host(self, ts: float, state: str | None, module_failed: bool,
                      proc_fps: float | None) -> list[Alert]:
        """Aturan HOST; ts = time.monotonic() milik CameraManager."""
        out = []
        for r in self.rules:
            if r.type == "camera_offline":
                cond = state is not None and state not in ("streaming", "file")
            elif r.type == "module_error":
                cond = module_failed
            elif r.type == "overload":
                cond = proc_fps is not None and r.threshold is not None and proc_fps < r.threshold
            else:
                continue
            if self._gate(r, cond, ts):
                out.append(self._alert(r, ts))
        return out
