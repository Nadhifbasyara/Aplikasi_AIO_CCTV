"""Fase 7: rule engine generik (gerbang for_s, cooldown, peran zona, jendela event, aturan host)."""
import pytest
import supervision as sv
from pydantic import ValidationError

from aio_cctv.alerts.rules import Rule, RuleEngine, default_rules, parse_rules
from aio_cctv.modules.api import Event, Metric, ModuleResult

ROLES = {"kasir": {"z1"}}


def res(metrics=(), events=()):
    return ModuleResult(sv.Detections.empty(), list(metrics), list(events))


def test_metric_gt_needs_duration_and_respects_cooldown():
    eng = RuleEngine([Rule(id="q", type="metric_gt", metric="n", region_role="kasir",
                           threshold=4, for_s=3, cooldown_s=10)])
    fired = [t for t in range(30) if eng.evaluate(res([Metric("n", 5, "z1")]), t, ROLES)]
    assert fired == [3, 13, 23]                           # mulai t=0, tunggu 3 s, jeda 10 s


def test_condition_break_resets_duration():
    eng = RuleEngine([Rule(id="q", type="metric_gt", metric="n", threshold=4, for_s=3)])
    for t, v in [(0, 5), (1, 5), (2, 1), (3, 5), (4, 5)]:
        assert eng.evaluate(res([Metric("n", v)]), t, {}) == []
    assert eng.evaluate(res([Metric("n", 5)]), 6, {})      # 3 s sejak t=3


def test_region_role_filters_zones():
    eng = RuleEngine([Rule(id="q", type="metric_gt", metric="n", region_role="kasir", threshold=1)])
    assert eng.evaluate(res([Metric("n", 9, "z2")]), 0, ROLES) == []      # z2 bukan kasir
    assert eng.evaluate(res([Metric("n", 9, "z1")]), 1, ROLES)[0].message.endswith("(nilai 9)")


def test_unknown_role_matches_nothing():
    eng = RuleEngine([Rule(id="q", type="metric_lt", metric="n", region_role="tidak_ada",
                           threshold=100)])
    assert eng.evaluate(res([Metric("n", 0, "z1")]), 0, ROLES) == []


def test_event_rule_counts_within_window():
    eng = RuleEngine([Rule(id="e", type="event", event="idle", min_count=3, window_s=10,
                           cooldown_s=0)])
    out = [bool(eng.evaluate(res(events=[Event("idle", t)]), t, {})) for t in (0, 5, 20, 25, 29)]
    assert out == [False, False, False, False, True]      # hanya 20, 25, 29 dalam 10 s


def test_host_rules():
    eng = RuleEngine(default_rules() + [Rule(id="fps", type="overload", threshold=8, for_s=10)])
    assert eng.evaluate_host(0, "reconnecting", False, None) == []        # belum 60 s
    ids = {a.rule_id for a in eng.evaluate_host(61, "reconnecting", True, None)}
    assert ids == {"offline", "modul"}
    assert eng.evaluate_host(100, "streaming", False, 5.0) == []
    assert [a.rule_id for a in eng.evaluate_host(111, "streaming", False, 5.0)] == ["fps"]


def test_disabled_rules_and_defaults():
    assert RuleEngine([Rule(id="x", type="module_error", enabled=False)]).rules == []
    assert [r.id for r in parse_rules([])] == ["offline", "modul"]
    assert parse_rules([{"id": "a", "type": "event", "event": "e"}])[0].window_s == 60


def test_invalid_rule_rejected():
    with pytest.raises(ValidationError):
        Rule(id="x", type="metric_kira_kira")
    with pytest.raises(ValidationError):
        Rule(id="x", type="event", window_s=0)
