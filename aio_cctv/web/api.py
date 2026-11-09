"""API & halaman dashboard web lokal (Fase 8 §8.2). Read-only, kecuali tandai-dibaca alert.

Semua angka berasal dari fungsi yang sama dengan dashboard desktop (storage/queries.py,
storage/dashboard_data.py), jadi web dan desktop selalu menampilkan angka yang sama.
"""
from __future__ import annotations

import io
import tempfile
import time
import zipfile
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path

import cv2
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from aio_cctv import __version__
from aio_cctv.annotate import render_heat
from aio_cctv.storage import queries as q
from aio_cctv.storage.dashboard_data import AGG_LABELS, default_widgets, load_dashboard
from aio_cctv.web.auth import COOKIE, SESSION_TTL_S, verify_password
from aio_cctv.web.state import WebState
from aio_cctv.zones.models import Profile

STATIC = Path(__file__).parent / "static"
OPEN_PATHS = ("/api/login", "/api/health")
MJPEG_MAX_FPS = 15


def _profile(view) -> Profile | None:
    try:
        return Profile.load(view.profile_path) if view.profile_path.exists() else None
    except Exception:                                   # profil rusak: dashboard tetap jalan
        return None


def _day_bounds(start: str | None, end: str | None, days: int = 7) -> tuple[float, float]:
    if not start or not end:
        return q.day_range(days)
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    return datetime.combine(s, dtime.min).timestamp(), datetime.combine(e, dtime.max).timestamp()


def create_app(state: WebState) -> FastAPI:
    app = FastAPI(title="AIO-CCTV Dashboard", version=__version__, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if (state.auth_required and path.startswith("/api/") and path not in OPEN_PATHS
                and not state.sessions.valid(request.cookies.get(COOKIE))):
            return JSONResponse({"detail": "perlu login"}, status_code=401)
        return await call_next(request)

    def camera_or_404(cam_id: str):
        if (v := state.camera(cam_id)) is None:
            raise HTTPException(404, "kamera tidak ditemukan")
        return v

    # ------------------------------------------------------------ sesi
    @app.get("/api/health")
    def health(request: Request):
        return {"app": "AIO-CCTV", "version": __version__, "auth_required": state.auth_required,
                "logged_in": (not state.auth_required)
                or state.sessions.valid(request.cookies.get(COOKIE)),
                "time": time.time()}

    @app.post("/api/login")
    def login(response: Response, password: str = Body(..., embed=True)):
        if not state.auth_required:
            return {"ok": True}
        if not state.password_hash or not verify_password(password, state.password_hash):
            time.sleep(1.0)                                     # perlambat tebak-tebakan
            raise HTTPException(401, "kata sandi salah")
        response.set_cookie(COOKIE, state.sessions.new(), max_age=int(SESSION_TTL_S),
                            httponly=True, samesite="strict")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        state.sessions.drop(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE)
        return {"ok": True}

    # ------------------------------------------------------------ kamera
    @app.get("/api/cameras")
    def cameras():
        unread: dict[str, int] = {}
        for a in q.alerts(state.db_path, 0, time.time() + 86400, unacked_only=True):
            unread[a["camera_id"]] = unread.get(a["camera_id"], 0) + 1
        out = []
        for v in sorted(state.cameras(), key=lambda c: c.name.lower()):
            m = state.manifest(v.module_id)
            s = v.stats
            out.append({"id": v.id, "name": v.name, "running": v.running,
                        "state": s.get("state", "berjalan" if v.running else "berhenti"),
                        "error": v.error, "module_id": v.module_id,
                        "module_name": m.name if m else f"{v.module_id} (tidak terpasang)",
                        "proc_fps": s.get("proc_fps"), "stream_fps": s.get("stream_fps"),
                        "detect_ms": s.get("detect_ms"), "module_ms": s.get("module_ms"),
                        "tracks": s.get("tracks"), "metrics": s.get("metrics", {}),
                        "unread_alerts": unread.get(v.id, 0),
                        "has_frame": state.frames.get(v.id) is not None})
        return out

    @app.get("/api/cameras/{cam_id}/snapshot.jpg")
    def snapshot(cam_id: str, w: int = 960):
        camera_or_404(cam_id)
        jpg = state.frames.jpeg(cam_id, max_width=max(160, min(w, 1920)))
        if jpg is None:
            raise HTTPException(404, "kamera tidak sedang berjalan")
        return Response(jpg[0], media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{cam_id}/live.mjpg")
    def live(cam_id: str, fps: float = 10, w: int = 960):
        """MJPEG ≤ 15 fps dari frame beranotasi terakhir. Hanya meng-encode saat ada frame
        baru dan selama ada penonton — beban tambahan nol bila halaman ditutup."""
        camera_or_404(cam_id)
        period = 1.0 / max(1.0, min(fps, MJPEG_MAX_FPS))

        def stream():
            last_seq, idle_since = -1, time.monotonic()
            while True:
                t0 = time.monotonic()
                f = state.frames.get(cam_id)
                if f is not None and f.seq != last_seq:
                    jpg = state.frames.jpeg(cam_id, max_width=max(160, min(w, 1920)))
                    if jpg is not None:
                        last_seq, idle_since = jpg[1], t0
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                               + str(len(jpg[0])).encode() + b"\r\n\r\n" + jpg[0] + b"\r\n")
                elif t0 - idle_since > 30:              # kamera berhenti: tutup koneksi
                    return
                time.sleep(max(0.0, period - (time.monotonic() - t0)))

        return StreamingResponse(stream(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{cam_id}/live")
    def live_values(cam_id: str):
        """Nilai metrik terakhir dari worker (diperbarui tiap detik). Dipanggil halaman kamera
        tiap 2 detik supaya KPI "sekarang" ikut bergerak tanpa memuat ulang seluruh dashboard."""
        v = camera_or_404(cam_id)
        return {"running": v.running, "state": v.stats.get("state"), "ts": time.time(),
                "live_metrics": v.stats.get("live_metrics", []) if v.running else []}

    @app.get("/api/cameras/{cam_id}/dashboard")
    def dashboard(cam_id: str, days: int = 1):
        v = camera_or_404(cam_id)
        m = state.manifest(v.module_id)
        if m is None:
            raise HTTPException(409, f"modul '{v.module_id}' tidak terpasang/valid")
        profile = _profile(v)
        widgets = (profile.dashboard if profile else []) or m.dashboard or default_widgets(m)
        start, end = q.day_range(max(1, min(days, 366)))
        live = v.stats.get("live_metrics") if v.running else None
        data = load_dashboard(state.db_path, cam_id, m, widgets, start, end, live)
        for d in data:                                  # ndarray/tuple -> JSON
            if "heat" in d:
                d["has_heat"] = d.pop("heat") is not None
            if "series" in d:
                d["series"] = {k: [list(xs), list(ys)] for k, (xs, ys) in d["series"].items()}
            if "uptime" in d:
                d["uptime"] = list(d["uptime"])
        return {"camera": {"id": v.id, "name": v.name}, "start": start, "end": end,
                "module": {"id": m.id, "name": m.name, "version": m.version},
                "metrics": {x.key: {"label": x.label or x.key, "unit": x.unit, "agg": x.agg,
                                    "agg_label": AGG_LABELS.get(x.agg, x.agg)} for x in m.metrics},
                "events": {e.type: e.label or e.type for e in m.events},
                "regions": profile.region_names if profile else {},
                "widgets": data}

    @app.get("/api/cameras/{cam_id}/heatmap.png")
    def heatmap(cam_id: str, days: int = 1, w: int = 640):
        v = camera_or_404(cam_id)
        m = state.manifest(v.module_id)
        start, end = q.day_range(max(1, min(days, 366)))
        heat = q.heat_sum(state.db_path, cam_id, m.id if m else v.module_id, start, end)
        if heat is None:
            raise HTTPException(404, "belum ada data heatmap")
        profile = _profile(v)
        fw, fh = profile.frame_size if profile and profile.frame_size else (16, 9)
        w = max(160, min(w, 1600))
        img = render_heat(heat, (w, max(1, round(w * fh / fw))))
        ok, buf = cv2.imencode(".png", img)
        return Response(buf.tobytes(), media_type="image/png")

    # ------------------------------------------------------------ alert
    @app.get("/api/alerts")
    def alerts(days: int = 7, cam: str | None = None, unread: bool = False):
        start, end = q.day_range(max(1, min(days, 366)))
        names = {v.id: v.name for v in state.cameras()}
        rows = q.alerts(state.db_path, start, end, cam, unread)
        for r in rows:
            r["camera_name"] = names.get(r["camera_id"], r["camera_id"])
        total_unread = len(q.alerts(state.db_path, 0, time.time() + 86400, None, True))
        return {"alerts": rows, "unread": total_unread}

    @app.post("/api/alerts/ack")
    def ack(ids: list[int] = Body(default=[], embed=True),
            ack_all: bool = Body(False, embed=True, alias="all")):
        if ack_all:
            ids = [a["id"] for a in q.alerts(state.db_path, 0, time.time() + 86400, None, True)]
        if ids:
            q.ack_alerts(state.db_path, ids)
        return {"acknowledged": len(ids)}

    # ------------------------------------------------------------ laporan
    def _report_cameras(cams: str | None):
        from aio_cctv.reports.pdf_report import CameraInfo

        wanted = set(cams.split(",")) if cams else None
        out = []
        for v in sorted(state.cameras(), key=lambda c: c.name.lower()):
            if wanted is None or v.id in wanted:
                p = _profile(v)
                out.append(CameraInfo(v.id, v.name, state.manifest(v.module_id),
                                      p.region_names if p else {}))
        if not out:
            raise HTTPException(400, "tidak ada kamera dipilih")
        return out

    @app.get("/api/reports/csv")
    def report_csv(start: str | None = None, end: str | None = None, cams: str | None = None):
        from aio_cctv.reports.csv_export import export_csv

        s, e = _day_bounds(start, end)
        cameras_ = _report_cameras(cams)
        buf = io.BytesIO()
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(buf, "w") as z:
            for f in export_csv(state.db_path, tmp, [c.id for c in cameras_], s, e):
                z.write(f, f.name)
        name = f"aio-cctv-csv-{datetime.fromtimestamp(s):%Y%m%d}-{datetime.fromtimestamp(e):%Y%m%d}.zip"
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/reports/html")
    def report_html(start: str | None = None, end: str | None = None, cams: str | None = None):
        """Laporan siap cetak di browser (gambar heatmap disisipkan sebagai data URI)."""
        import base64

        from aio_cctv.reports.pdf_report import build_report

        s, e = _day_bounds(start, end)
        html, images = build_report(state.db_path, _report_cameras(cams), s, e, __version__)
        for name, img in images.items():
            ok, png = cv2.imencode(".png", img)
            html = html.replace(f"src='{name}'",
                                f"src='data:image/png;base64,{base64.b64encode(png).decode()}'")
        return Response(html, media_type="text/html")

    @app.get("/api/reports/pdf")
    def report_pdf(start: str | None = None, end: str | None = None, cams: str | None = None):
        from aio_cctv.reports.pdf_report import build_report, html_to_pdf

        s, e = _day_bounds(start, end)
        html, images = build_report(state.db_path, _report_cameras(cams), s, e, __version__)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "laporan.pdf"
            try:
                html_to_pdf(html, str(path), images)    # QPdfWriter: aman di thread non-GUI
            except Exception as ex:
                raise HTTPException(503, f"PDF tidak tersedia ({ex}); gunakan laporan HTML") from ex
            data = path.read_bytes()
        name = f"laporan-aio-cctv-{datetime.fromtimestamp(s):%Y%m%d}-{datetime.fromtimestamp(e):%Y%m%d}.pdf"
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ------------------------------------------------------------ halaman
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
