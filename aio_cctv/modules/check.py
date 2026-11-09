"""Uji kepatuhan & paritas paket modul (Fase 6 §6). Dipakai scripts/check_module.py dan GUI."""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np

from aio_cctv.modules.host import result_to_record, validate_result
from aio_cctv.modules.registry import inspect
from aio_cctv.zones.models import Line, Profile, SourceConfig, Zone

VIDEO_EXT = (".mp4", ".avi", ".mkv", ".mov")


def synthetic_profile(manifest) -> Profile:
    """Profil uji bila pengguna tidak memberi profil: satu zona per peran zona (atau satu zona
    seluruh frame) dan satu garis per peran garis (atau satu garis horizontal di tengah)."""
    zone_roles = [r.role for r in manifest.regions if r.kind == "zone"] or [None]
    line_roles = [r.role for r in manifest.regions if r.kind == "line"] or [None]
    zones = [Zone(id=f"z{i}", name=role or f"Zona {i}", role=role,
                  polygon=[(0.02, 0.02), (0.98, 0.02), (0.98, 0.98), (0.02, 0.98)])
             for i, role in enumerate(zone_roles, 1)]
    lines = [Line(id=f"l{i}", name=role or f"Garis {i}", role=role, p1=(0.0, 0.5), p2=(1.0, 0.5))
             for i, role in enumerate(line_roles, 1)]
    return Profile(profile_id="check", source=SourceConfig(uri=""), zones=zones, lines=lines)


def _close(a, b, tol: float) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return (a is not None and b is not None) and math.isclose(a, b, rel_tol=tol, abs_tol=tol)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    return a == b


def compare_records(got: list[dict], expected: list[dict], tol: float = 1e-6) -> dict:
    """Bandingkan dua keluaran JSONL per frame. Kosong di 'mismatch' = paritas lulus."""
    report = {"frames": (len(got), len(expected)), "mismatch": {}, "first": None,
              "events_by_type": {}}
    for g, e in zip(got, expected):
        for key in ("tracks", "metrics", "events"):
            if not _close(g[key], e[key], tol):
                report["mismatch"][key] = report["mismatch"].get(key, 0) + 1
                if report["first"] is None:
                    report["first"] = {"frame": e["frame"], "aspek": key,
                                       "acuan": e[key], "hasil": g[key]}
    cg, ce = _count_events(got), _count_events(expected)
    report["events_by_type"] = {t: (cg.get(t, 0), ce.get(t, 0)) for t in sorted(cg.keys() | ce.keys())}
    if len(got) != len(expected):
        report["mismatch"]["jumlah_frame"] = abs(len(got) - len(expected))
    return report


def _count_events(records: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        for ev in r["events"]:
            out[ev["type"]] = out.get(ev["type"], 0) + 1
    return out


def _frames(video: str | None, n: int):
    """Frame dari video, atau frame hitam 640×360 bila tidak ada video (uji cepat)."""
    if video is None:
        for i in range(n):
            yield i + 1, i / 10.0, np.zeros((360, 640, 3), dtype=np.uint8)
        return
    from aio_cctv.sources.file_source import FileSource
    src = FileSource(video)
    try:
        while (f := src.read()) is not None and (not n or f.seq <= n):
            yield f.seq, f.ts, f.image
    finally:
        src.stop()


def run_checks(module_dir: str | Path, video: str | None = None, frames: int = 300,
               profile: Profile | None = None, expected: str | Path | None = None,
               device: str | None = None, record_to: str | Path | None = None,
               log: Callable[[str], None] = print) -> bool:
    ok = True

    def report(passed: bool, msg: str) -> None:
        nonlocal ok
        ok &= passed
        log(f"[{'OK' if passed else 'GAGAL'}] {msg}")

    info = inspect(module_dir)
    m = info.manifest
    if m is None:
        report(False, "manifest tidak valid: " + "; ".join(info.errors))
        return False
    report(True, f"manifest valid ({m.id} {m.version}, api {m.api_version})")
    file_errors, req_errors = m.check_files(), m.check_requirements()
    report(not file_errors, "file lengkap" if not file_errors else "; ".join(file_errors))
    report(not req_errors, "requirements terpenuhi" if not req_errors else "; ".join(req_errors))
    if file_errors:
        return False

    if video is None:
        samples = sorted(p for p in (m.dir / "samples").glob("*") if p.suffix.lower() in VIDEO_EXT)
        video = str(samples[0]) if samples else None
    if video is None:
        log("[INFO] tidak ada video (samples/ kosong, --video tidak diberikan): "
            "uji memakai 5 frame hitam, paritas & waktu tidak bermakna")
        frames = 5

    try:
        from aio_cctv.inference.detector import Detector
        det = Detector.from_manifest(m, device)
        cls = {i: det.names[i] for i in det.class_ids} if det.class_ids else det.names
        report(True, f"bobot dimuat: kelas = {cls}, imgsz {m.model.imgsz}")
    except Exception as e:
        report(False, f"bobot tidak bisa dimuat: {e}")
        return False

    from aio_cctv.modules.host import ModuleError, ModuleRunner, build_context
    profile = profile or synthetic_profile(m)
    gen = _frames(video, frames)
    try:
        first = next(gen)
    except StopIteration:
        report(False, f"video {video} tidak berisi frame")
        return False
    h, w = first[2].shape[:2]
    fps = 10.0
    if video is not None:
        import cv2
        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS) or fps
        cap.release()
    try:
        runner = ModuleRunner(m, build_context(m, profile, "check", (w, h), fps))
        report(True, f"entrypoint {m.entrypoint} dapat diinstansiasi")
    except ModuleError as e:
        report(False, str(e))
        return False

    import time
    records, problems, det_ms, mod_ms, n, ts = [], [], [], [], 0, 0.0
    try:
        for seq, ts, img in [first, *gen]:
            t0 = time.perf_counter()
            d = det(img)
            det_ms.append((time.perf_counter() - t0) * 1e3)
            res = runner.process(img, d, ts)
            mod_ms.append(runner.module_ms)
            problems += validate_result(m, res)
            records.append(result_to_record(seq, ts, res))
            n += 1
        closing = runner.close(ts)
        problems += validate_result(m, None, closing)
        if closing:
            records.append(result_to_record(-1, ts, None, closing))
        report(True, f"{n} frame {Path(video).name if video else '(frame hitam)'}: "
                     "tidak ada exception")
    except ModuleError as e:
        report(False, f"{e} (frame ke-{n + 1})")
        return False

    uniq = sorted(set(problems))
    report(not uniq, "semua Metric.key, Event.type & Event.data sesuai kontrak" if not uniq
           else "pelanggaran kontrak: " + "; ".join(uniq[:5]))
    if det_ms and video is not None:
        d50, d95, m50, m95 = (float(np.percentile(xs, q))
                              for xs in (det_ms, mod_ms) for q in (50, 95))
        log(f"[INFO] detect_ms p50 {d50:.1f} / p95 {d95:.1f} · "
            f"module_ms p50 {m50:.1f} / p95 {m95:.1f}")

    if record_to:
        Path(record_to).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                           for r in records), encoding="utf-8")
        log(f"[INFO] keluaran ditulis ke {record_to}")
    if expected:
        exp = [json.loads(line) for line in Path(expected).read_text(encoding="utf-8").splitlines()
               if line.strip()]
        rep = compare_records(records, exp)
        passed = not rep["mismatch"]
        report(passed, f"paritas vs {Path(expected).name}: "
                       + ("identik" if passed else f"berbeda {rep['mismatch']}"))
        log(f"[INFO] event per tipe (hasil, acuan): {rep['events_by_type']}")
        if rep["first"]:
            log(f"[INFO] perbedaan pertama: {json.dumps(rep['first'], ensure_ascii=False)[:500]}")
    return ok
