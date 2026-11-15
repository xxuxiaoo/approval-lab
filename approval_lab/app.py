import csv
import io
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .benchmark import builtin_cases, metadata
from .pricing import PRICING_PRESETS
from .providers import ProviderClient, ProviderError
from .runner import RunEngine, next_due, result_rows
from .schemas import Pricing, ProviderInput, RunInput, ScheduleInput
from .storage import InstanceLock, Store, dumps, now_iso

STATIC = Path(__file__).parent / "static"


def create_app(data_dir=None, client_factory=ProviderClient, scheduler=True):
    @asynccontextmanager
    async def lifespan(app):
        directory = Path(data_dir or os.environ.get("APPROVAL_LAB_DATA_DIR", "data"))
        instance = InstanceLock(directory)
        instance.acquire()
        try:
            store = Store(directory)
            engine = RunEngine(store, client_factory)
            app.state.store, app.state.engine = store, engine
            if scheduler:
                await engine.start()
            try:
                yield
            finally:
                await engine.shutdown()
        finally:
            instance.release()

    app = FastAPI(title="Approval Lab", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        if host not in ("127.0.0.1", "localhost", "::1"):
            return JSONResponse({"detail": "仅允许 localhost 访问"}, status_code=403)
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            own_origin = f"{request.url.scheme}://{request.headers.get('host')}"
            if (origin and origin != own_origin) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if request.headers.get("x-approval-lab") != "1":
                    return JSONResponse({"detail": "缺少本地请求标记"}, status_code=403)
                if request.headers.get("content-length", "0") != "0" and "application/json" not in request.headers.get("content-type", ""):
                    return JSONResponse({"detail": "仅接受 application/json"}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        return response

    @app.exception_handler(ValueError)
    async def value_error(_request, exc):
        return JSONResponse({"detail": str(exc)[:1000]}, status_code=400)

    @app.exception_handler(ProviderError)
    async def provider_error(_request, exc):
        return JSONResponse({"detail": str(exc)[:1000]}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        # Pydantic's default error body echoes submitted inputs, including API keys.
        return JSONResponse({"detail": "; ".join(
            ".".join(map(str, e["loc"][1:])) + ": " + e["msg"] for e in exc.errors()
        )[:1500]}, status_code=422)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/meta")
    async def meta():
        return {"version": __version__, "benchmark": metadata(), "pricing_presets": PRICING_PRESETS,
                "pricing_checked_at": "2026-11-15", "scheduler_requires_running_app": True}

    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": __version__, "scheduler_error": app.state.engine.scheduler_error}

    @app.get("/api/providers")
    async def providers():
        return app.state.store.providers()

    @app.get("/api/overview")
    async def overview():
        return app.state.store.one(
            "SELECT count(*) AS total_runs,coalesce(sum(json_extract(summary,'$.unsafe_allows')),0) "
            "AS unsafe_allows FROM runs"
        )

    @app.post("/api/providers", status_code=201)
    async def add_provider(value: ProviderInput):
        return app.state.store.save_provider(value)

    @app.put("/api/providers/{provider_id}")
    async def edit_provider(provider_id: int, value: ProviderInput):
        return app.state.store.save_provider(value, provider_id)

    @app.delete("/api/providers/{provider_id}")
    async def delete_provider(provider_id: int):
        store = app.state.store
        store.provider(provider_id)
        for run in store.query(
            "SELECT id FROM runs WHERE status IN ('queued','running') AND json_extract(config,'$.provider_id')=?",
            (provider_id,),
        ):
            app.state.engine.stop(run["id"])
        with store.connect() as conn:
            for row in conn.execute("SELECT id,spec FROM schedules").fetchall():
                if json.loads(row["spec"])["config"]["provider_id"] == provider_id:
                    conn.execute("UPDATE schedules SET enabled=0,last_error=? WHERE id=?",
                                 ("中转站已删除；请重新配置。", row["id"]))
            conn.execute("DELETE FROM providers WHERE id=?", (provider_id,))
        return {"ok": True}

    @app.post("/api/providers/{provider_id}/models")
    async def fetch_models(provider_id: int):
        async with client_factory(app.state.store.provider(provider_id, secret=True)) as client:
            return await client.list_models()

    @app.get("/api/cases")
    async def cases(mode: str = "full"):
        return [c.model_dump() for c in builtin_cases() if mode != "light" or c.light]

    @app.post("/api/runs/preview")
    async def preview_run(config: RunInput):
        selected, _provider, total = app.state.engine.validate(config)
        return {"requests": total, "unique_cases": len(selected),
                "risky_cases": sum(c.expected != "allow" for c in selected),
                "safe_cases": sum(c.expected == "allow" for c in selected)}

    @app.post("/api/runs", status_code=201)
    async def create_run(config: RunInput):
        run_id = app.state.engine.create_run(config)
        return {"id": run_id}

    def get_run(run_id):
        row = app.state.store.one("SELECT * FROM runs WHERE id=?", (run_id,))
        if not row:
            raise HTTPException(404, "测试记录不存在")
        for key in ("config", "snapshot", "summary"):
            row[key] = json.loads(row[key])
        return row

    @app.get("/api/runs")
    async def list_runs(limit: int = 200, offset: int = 0):
        rows = app.state.store.query("SELECT id FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                                     (max(1, min(limit, 1000)), max(0, offset)))
        return [get_run(row["id"]) for row in rows]

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str):
        run = get_run(run_id)
        run["results"] = result_rows(app.state.store, run_id)
        return run

    @app.post("/api/runs/{run_id}/stop")
    async def stop_run(run_id: str):
        get_run(run_id)
        return {"stopping": app.state.engine.stop(run_id),
                "note": "已发出的请求会等待返回以记录用量；不再启动后续请求。"}

    @app.put("/api/runs/{run_id}/pricing")
    async def update_pricing(run_id: str, pricing: Pricing):
        run = get_run(run_id)
        if run["status"] in ("queued", "running"):
            raise HTTPException(409, "请等待测试结束后补填账单价格")
        snapshot = run["snapshot"]
        snapshot.setdefault("initial_pricing", run["config"]["pricing"])
        snapshot.setdefault("pricing_revisions", []).append({"at": now_iso(), "pricing": pricing.model_dump()})
        run["config"]["pricing"] = pricing.model_dump()
        app.state.store.execute("UPDATE runs SET config=?,snapshot=? WHERE id=?",
                                (dumps(run["config"]), dumps(snapshot), run_id))
        app.state.engine.refresh_summary(run_id)
        return get_run(run_id)

    @app.get("/api/runs/{run_id}/export")
    async def export_run(run_id: str, format: str = "json"):
        run = get_run(run_id)
        run["results"] = result_rows(app.state.store, run_id)
        if format == "csv":
            output = io.StringIO(newline="")
            fields = ["case_id", "title", "category", "repeat_index", "expected", "decision", "error",
                      "reason", "latency_ms", "input_tokens", "output_tokens", "cache_read_tokens",
                      "cache_write_tokens", "returned_model", "request_id"]
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for result in run["results"]:
                item = {name: result.get(name, "") for name in fields}
                item.update({name: result["case"][name] for name in ("title", "category", "expected")})
                item.update({name: result["usage"].get(name, 0) for name in
                             ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")})
                # Prevent spreadsheet formula injection from provider-controlled reasons/model IDs.
                item = {key: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                        else value for key, value in item.items()}
                writer.writerow(item)
            content = "\ufeff" + output.getvalue()
            media_type = "text/csv"
        else:
            format = "json"
            content = json.dumps(run, ensure_ascii=False, indent=2)
            media_type = "application/json"
        return Response(content, media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="approval-lab-{run_id}.{format}"'})

    @app.get("/api/schedules")
    async def schedules():
        rows = app.state.store.query("SELECT * FROM schedules ORDER BY id DESC")
        for row in rows:
            row["spec"] = json.loads(row["spec"])
            row["enabled"] = bool(row["enabled"])
        return rows

    @app.post("/api/schedules", status_code=201)
    async def create_schedule(value: ScheduleInput):
        if not value.group_acknowledged:
            raise ValueError("请确认：分组由中转后台决定，定时任务无法自动切换或核验分组。")
        app.state.engine.validate(value.config)
        spec = value.model_dump()
        schedule_id = app.state.store.execute(
            "INSERT INTO schedules(name,spec,enabled,next_run_at,created_at) VALUES(?,?,?,?,?)",
            (value.name, dumps(spec), int(value.enabled), next_due(spec), now_iso()),
        )
        return {"id": schedule_id}

    @app.post("/api/schedules/{schedule_id}/toggle")
    async def toggle_schedule(schedule_id: int):
        row = app.state.store.one("SELECT * FROM schedules WHERE id=?", (schedule_id,))
        if not row:
            raise HTTPException(404, "定时任务不存在")
        enabled = not bool(row["enabled"])
        if enabled:
            app.state.engine.validate(RunInput.model_validate(json.loads(row["spec"])["config"]))
        app.state.store.execute("UPDATE schedules SET enabled=?,next_run_at=?,last_error=NULL WHERE id=?",
                                (int(enabled), next_due(json.loads(row["spec"])), schedule_id))
        return {"enabled": enabled}

    @app.delete("/api/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: int):
        app.state.store.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
