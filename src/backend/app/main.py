from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
import asyncio
import json
import logging
import secrets
from typing import Literal
from app.schemas.asset_import import AssetDataBatch, BatchCommit
from app.services.asset_imports import catalog as import_catalog
import sqlite3
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.domain.finance import Problem, require
from app.persistence.store import Store, pack
from app.services.workbench import Workbench
from app.providers.demo import seed
from app.providers.base import capabilities
from app.providers.public_nav import SUPPORTED
from app.version import VERSION
from app.runtime import workspace_id
from app.domain.daily_agenda import daily_agenda

ROOT = Path(__file__).resolve().parents[3]


def response(value, status=200):
    return JSONResponse(json.loads(pack(value)), status_code=status)


def create_app(root: Path = ROOT, testing=False, resources: Path | None = None):
    resources = resources or root
    lock = RLock()
    workbenches = {mode: Workbench(Store(root / "data" / mode / "workbench.sqlite3", mode), root) for mode in ["demo", "real"]}
    seed(workbenches["demo"])
    sessions = {}

    def run(mode, callback):
        require(mode in workbenches, "INVALID_MODE", "模式须为 demo 或 real")
        with lock:
            return callback(workbenches[mode])

    def sync_all():
        with lock:
            for w in workbenches.values():
                with w.store.transaction() as c:
                    w.auto_post_orders(c)
                    w.t_review_maintenance(c)
                    for fund in Store.docs(c, "fund"):
                        w.resolve_predictions(c, fund["id"])
                    key = "local-check:" + w.today()
                    c.execute("INSERT INTO jobs VALUES(?,?,?) ON CONFLICT(job_key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                              (key, pack({"status": "LOCAL_CACHE_CHECKED", "mode": w.mode, "real_provider": "AUTO_VERIFIED_FUNDS", "checked_at": w.now()}), w.now()))
                w.cleanup_storage_due()

    async def scheduler():
        while True:
            await asyncio.to_thread(sync_all)
            await asyncio.sleep(60)

    def collect_market():
        return workbenches["real"].collect_market_due()

    async def market_scheduler():
        while True:
            try:
                await asyncio.to_thread(collect_market)
            except Exception as exc:
                # Do not log fund positions, requests or source response bodies.
                logging.warning("Market collection cycle failed (%s)", type(exc).__name__)
            await asyncio.sleep(15)

    async def forecast_scheduler():
        while True:
            try:
                # Sources and fitting run outside the UI lock. The service
                # checks a bounded maintenance clock and immutable NAV basis.
                await asyncio.to_thread(workbenches["real"].forecast_maintenance)
            except Exception as exc:
                logging.warning("Forecast maintenance failed (%s)", type(exc).__name__)
            await asyncio.sleep(60)

    async def safety_scheduler():
        while True:
            try:
                await asyncio.to_thread(workbenches['real'].automatic_backup_due)
                await asyncio.to_thread(workbenches['real'].capture_estimate_results)
            except Exception as exc:
                logging.warning('Safety snapshot failed (%s)', type(exc).__name__)
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(app):
        tasks = [asyncio.create_task(scheduler()), asyncio.create_task(market_scheduler()), asyncio.create_task(forecast_scheduler()), asyncio.create_task(safety_scheduler())] if not testing else []
        yield
        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="FundDesk · 本地基金工作台", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.workbenches = workbenches
    app.state.lock = lock
    app.state.collect_market = collect_market
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"] + (["testserver"] if testing else []))

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return response({"code": exc.code, "message": exc.message, "field_errors": {}, "retryable": False}, exc.status)

    @app.exception_handler(KeyError)
    async def missing_handler(request, exc):
        return response({"code": "FIELD_REQUIRED", "message": "缺少必填字段：" + str(exc), "field_errors": {}}, 400)

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        return response({"code": "INVALID_INPUT", "message": "输入格式无效，请核对日期、时间和数值字段。", "field_errors": {}}, 400)

    @app.exception_handler(sqlite3.DatabaseError)
    async def database_handler(request, exc):
        logging.error("Database operation failed (%s)", type(exc).__name__)
        return response({"code": "DATABASE_ERROR", "message": "数据库操作失败，未确认的事务已回滚。请检查磁盘与诊断状态。"}, 503)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("sec-fetch-site") == "cross-site":
            return response({"code": "ORIGIN_DENIED", "message": "拒绝跨站访问本地应用"}, 403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return response({"code": "ORIGIN_DENIED", "message": "请求来源与本地应用不一致"}, 403)
        if request.url.path.startswith("/api/") and request.url.path not in {"/api/v1/session", "/api/v1/health"}:
            session = sessions.get(request.cookies.get("funding_session", ""))
            if not session:
                return response({"code": "SESSION_REQUIRED", "message": "本地会话已过期，请刷新页面"}, 401)
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session):
                return response({"code": "CSRF_INVALID", "message": "写请求校验失败，请刷新页面"}, 403)
        if request.method in {"POST", "PUT", "PATCH"}:
            if len(await request.body()) > 2_200_000:
                return response({"code": "REQUEST_TOO_LARGE", "message": "请求超过此接口大小限制"}, 413)
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "no-referrer"
        result.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            result.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/api/v1/session")
    def session(request: Request):
        id = request.cookies.get("funding_session")
        if id not in sessions:
            id = secrets.token_urlsafe(32)
            if len(sessions) > 100:
                sessions.clear()
            sessions[id] = secrets.token_urlsafe(32)
        result = response({"csrf_token": sessions[id], "version": VERSION})
        result.set_cookie("funding_session", id, httponly=True, samesite="strict", max_age=86400)
        return result

    @app.get("/api/v1/health")
    def health():
        return response({"status": "ok", "app": "funding-workbench", "version": VERSION, "database": "ready", "instance_id": os.environ.get("FUNDING_INSTANCE_ID", "test"),
                         "workspace_id":workspace_id(root),"owner":os.environ.get("FUNDING_OWNER","development"),
                         "providers": {"demo": "ready", "manual": "ready", "real_network": "AUTO_VERIFIED_FUNDS", "verified_codes": sorted(SUPPORTED)}})

    @app.get("/api/v1/state")
    def state(mode: str = "demo"):
        return response(run(mode, lambda w: w.snapshot()))

    @app.get('/api/v1/daily-agenda')
    def agenda(mode: str='real'):
        def build(w):
            snapshot=w.snapshot()
            names={r['fund_id']:r for r in snapshot['fund_sidebar']['funds']}
            view=dict(snapshot,funds=[dict(f,sidebar=names.get(f['id'],{})) for f in snapshot['funds']])
            return daily_agenda(view,w.daily_t_references(snapshot))
        return response(run(mode,build))

    @app.get('/api/v1/reconciliations')
    def reconciliations(mode: str='real'):
        return response(run(mode,lambda w:w.reconciliation_catalog()))

    @app.post('/api/v1/funds/{id}/reconciliation/preview')
    def reconciliation_preview(id:str,p:dict,mode:str='real'):
        return response(run(mode,lambda w:w.preview_reconciliation(id,p)))

    @app.post('/api/v1/funds/{id}/reconciliation')
    def reconciliation_save(id:str,p:dict,mode:str='real'):
        return response(run(mode,lambda w:w.save_reconciliation(id,p)))

    @app.get('/api/v1/funds/{id}/t-review')
    def t_review(id:str,mode:str='real'):
        return response(run(mode,lambda w:w.t_review(id)))

    @app.post('/api/v1/funds/{id}/t-review/baseline')
    def t_review_start(id:str,p:dict,mode:str='real'):
        return response(run(mode,lambda w:w.start_t_review(id,p)))

    @app.get('/api/v1/maintenance')
    def maintenance(mode:str='real'):
        return response(run(mode,lambda w:w.maintenance_status()))

    @app.get('/api/v1/valuation-explanation')
    def portfolio_explanation(mode:str='real'):
        return response(run(mode,lambda w:w.valuation_explanation()))

    @app.get('/api/v1/funds/{id}/valuation-explanation')
    def fund_explanation(id:str,mode:str='real'):
        return response(run(mode,lambda w:w.valuation_explanation(id)))

    @app.get('/api/v1/portfolio-risk')
    def portfolio_risk(mode:str='real'):
        return response(run(mode,lambda w:w.portfolio_risk()))

    @app.get('/api/v1/forecasting/scorecard')
    def forecast_scorecard(mode:str='real'):
        return response(run(mode,lambda w:w.forecast_scorecard()))

    @app.post('/api/v1/funds/{id}/plan-scenarios')
    def plan_scenarios(id:str,p:dict,mode:str='real'):
        return response(run(mode,lambda w:w.plan_scenarios(id,p)))

    @app.put("/api/v1/fund-sidebar")
    def fund_sidebar(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.save_fund_sidebar(p)))

    @app.get("/api/v1/providers")
    def providers():
        return response(capabilities())

    @app.get("/api/v1/storage")
    def storage(mode: str = "demo"):
        return response(run(mode, lambda w: w.storage_status()))

    @app.post("/api/v1/storage/cleanup")
    def cleanup_storage(mode: str = "demo"):
        return response(run(mode, lambda w: w.cleanup_storage()))

    @app.post("/api/v1/funds")
    def fund(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.save_fund(p)))

    @app.put("/api/v1/funds/{id}/rules")
    def rules(id: str, p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.save_rules(id, p)))

    @app.put("/api/v1/funds/{id}/strategy")
    def strategy(id: str, p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.save_strategy(id, p)))

    @app.put("/api/v1/funds/{id}/t-profile")
    def t_profile(id: str, p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.save_t_profile(id, p)))

    @app.put("/api/v1/funds/{id}/investment-plan")
    def investment_plan(id: str, p: dict, mode: str = "real"):
        return response(run(mode, lambda w: w.save_investment_plan(id, p)))

    @app.post("/api/v1/observations")
    def observations(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.observe(p)))

    @app.post("/api/v1/statements")
    def statement(p: dict, mode: str = "real"):
        return response(run(mode, lambda w: w.save_statement(p)))

    @app.post("/api/v1/funds/{id}/nav/refresh")
    def refresh_nav(id: str, mode: str = "real"):
        return response(run(mode, lambda w: w.refresh_public_nav(id, manual=True)))

    @app.post("/api/v1/funds/{id}/market/refresh")
    def refresh_market(id: str, mode: str = "real"):
        require(mode in workbenches, "INVALID_MODE", "模式须为 demo 或 real")
        # This service uses short SQLite transactions internally. Holding the
        # request lock over its HTTP calls would block state/forms for seconds.
        return response(workbenches[mode].refresh_market(id, manual=True))

    @app.post("/api/v1/market/refresh-all")
    def refresh_all_market(mode: str = "real"):
        require(mode in workbenches, "INVALID_MODE", "模式须为 demo 或 real")
        return response(workbenches[mode].refresh_all_market())

    @app.post("/api/v1/funds/{id}/t-analysis")
    def t_analysis(id: str, p: dict, mode: str = "real"):
        return response(run(mode, lambda w: w.preview_t_analysis(id, p)))

    @app.put("/api/v1/funds/{id}/t-pairings")
    def t_pairings(id: str, p: dict, mode: str = "real"):
        return response(run(mode, lambda w: w.save_t_pairings(id, p)))

    @app.post('/api/v1/funds/{id}/bucket-analysis')
    def bucket_analysis(id: str,p: dict,mode: str='real'):
        return response(run(mode,lambda w:w.preview_bucket_analysis(id,p)))

    @app.post('/api/v1/funds/{id}/tracking/preview')
    def preview_tracking(id: str,p: dict,mode: str='real'):
        return response(run(mode,lambda w:w.preview_tracking(id,p)))

    @app.post('/api/v1/funds/{id}/tracking/start')
    def start_tracking(id: str,p: dict,mode: str='real'):
        return response(run(mode,lambda w:w.initialize_tracking(id,p)))

    @app.put('/api/v1/funds/{id}/bucket-classification')
    def bucket_classification(id: str,p: dict,mode: str='real'):
        return response(run(mode,lambda w:w.save_bucket_classification(id,p)))

    @app.post("/api/v1/events")
    def events(p: dict, mode: str = "demo"):
        require(p["kind"] not in {"REVERSAL", "ORDER_SUBMITTED"}, "INVALID_EVENT", "请使用专用更正/申请入口")
        return response(run(mode, lambda w: w.event(p["kind"], p["payload"], p["key"], p["occurred_at"], p.get("expected_revision"))))

    @app.post("/api/v1/orders")
    def orders(p: dict, mode: str = "demo"):
        def register(w):
            result = w.order(p)
            result["automation"] = w.auto_post_orders()
            result["revision"] = result["automation"].get("revision", result["revision"])
            return result
        return response(run(mode, register))

    @app.post("/api/v1/orders/process")
    def process_orders(mode: str = "real"):
        def process(w):
            result=w.auto_post_orders()
            result['t_review']=w.t_review_maintenance()
            return result
        return response(run(mode, process))

    @app.post("/api/v1/orders/{id}/edit")
    def edit_order(id: str, p: dict, mode: str = "real"):
        return response(run(mode, lambda w: w.edit_order(id, p)))

    @app.post('/api/v1/funds/{id}/sell-preview')
    def sell_preview(id: str, p: dict, mode: str = 'real'):
        return response(run(mode, lambda w: w.preview_sell(id, p)))

    @app.post("/api/v1/orders/preview")
    def preview_order(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.preview_order(p)))

    @app.post("/api/v1/corrections")
    def corrections(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.correct(p)))

    @app.post("/api/v1/recommendations/evaluate")
    def recommendations(mode: str = "demo"):
        return response(run(mode, lambda w: w.recommendations()))

    @app.get("/api/v1/recommendations/current")
    def current_recommendations(mode: str = "demo"):
        return response(run(mode, lambda w: w.recommendations(persist=False)))

    @app.get("/api/v1/research")
    def research(mode: str = "demo"):
        return response(run(mode, lambda w: w.research_dashboard()))

    @app.post("/api/v1/research/data/refresh")
    def research_data_refresh(mode: str = "real"):
        workbench = run(mode, lambda w: w)
        return response(workbench.refresh_research_data())

    @app.put("/api/v1/research/policy")
    def research_policy(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.save_research_policy(p)))

    @app.post("/api/v1/research/runs")
    def research_generate(p: dict, mode: str = "demo"):
        workbench = run(mode, lambda w: w)
        return response(workbench.generate_research(p))

    @app.get("/api/v1/research/runs/{id}/export")
    def research_export(id: str, mode: str = "demo"):
        result = response(run(mode, lambda w: w.research_run_export(id)))
        result.headers["Content-Disposition"] = f'attachment; filename="{id}.json"'
        return result

    @app.get("/api/v1/research/runs/{id}")
    def research_read(id: str, mode: str = "demo"):
        return response(run(mode, lambda w: w.research_get_run(id)))

    @app.post("/api/v1/research/snapshots/preview")
    def research_input_preview(p: dict, mode: str = "demo"):
        workbench = run(mode, lambda w: w)
        return response(workbench.import_research_input(p, preview=True))

    @app.get("/api/v1/research/contracts/input")
    def research_schema(mode: str = "demo"):
        result = response(run(mode, lambda w: w.research_input_schema()))
        result.headers["Content-Disposition"] = 'attachment; filename="funddesk-research-input.schema.json"'
        return result

    @app.post("/api/v1/research/snapshots")
    def research_input_commit(p: dict, mode: str = "demo"):
        workbench = run(mode, lambda w: w)
        return response(workbench.import_research_input(p))

    @app.get("/api/v1/predictions")
    def predictions(mode: str = "demo"):
        return response(run(mode, lambda w: w.prediction_list()))

    @app.get("/api/v1/forecasting")
    def forecasting(mode: str = "real"):
        return response(run(mode, lambda w: w.forecasting_dashboard()))

    @app.post("/api/v1/forecasting/runs")
    def forecast_generate(p: dict, mode: str = "real"):
        workbench = run(mode, lambda w: w)
        return response(workbench.generate_forecasts(p))

    @app.get("/api/v1/forecasting/runs/{id}/export")
    def forecast_export(id: str, mode: str = "real"):
        result = response(run(mode, lambda w: w.forecast_run_export(id)))
        result.headers["Content-Disposition"] = f'attachment; filename="{id}.json"'
        return result

    @app.post("/api/v1/predictions")
    def predict(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.predict(p)))

    @app.get("/api/v1/predictions/evaluate/{fund_id}")
    def evaluate(fund_id: str, method: str = "LAST_VALUE", mode: str = "demo"):
        return response(run(mode, lambda w: w.evaluate_prediction(fund_id, method)))

    @app.post("/api/v1/imports/preview")
    def import_preview(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.import_preview(p["kind"], p["content"])))

    @app.post("/api/v1/imports/commit")
    def import_commit(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.import_commit(p["id"], p["source_hash"])))

    @app.get('/api/v1/imports/catalog')
    def asset_import_catalog():
        return response(import_catalog())

    @app.get('/api/v1/imports/templates/{kind}')
    def asset_import_template(kind: Literal['fund','gold']):
        path = resources / 'input/templates' / (kind + '_import.json')
        require(path.is_file(),'TEMPLATE_NOT_FOUND','模板尚未安装')
        return FileResponse(path, media_type='application/json', filename=path.name)

    @app.post('/api/v1/imports/batches/preview')
    def asset_preview(p: AssetDataBatch, mode: Literal['real','demo']):
        return response(run(mode, lambda w: w.preview_asset_batch(p.model_dump(mode='json'))))

    @app.get('/api/v1/imports/batches')
    def asset_batches(mode: Literal['real','demo'], limit: int = 20, offset: int = 0):
        return response(run(mode, lambda w: w.asset_batches(limit,offset)))

    @app.get('/api/v1/imports/batches/{id}')
    def asset_batch(id: str, mode: Literal['real','demo']):
        return response(run(mode, lambda w: w.asset_batch(id)))

    @app.post('/api/v1/imports/batches/{id}/commit')
    def asset_commit(id: str, p: BatchCommit, mode: Literal['real','demo']):
        return response(run(mode, lambda w: w.commit_asset_batch(id,p.model_dump())))

    @app.get('/api/v1/imports/assets/{id}')
    def imported_asset(id: str, mode: Literal['real','demo']):
        return response(run(mode, lambda w: w.imported_asset(id)))

    @app.get('/api/v1/gold-products')
    def gold_products(mode: Literal['real','demo']):
        return response(run(mode, lambda w: w.gold_products()))

    @app.get("/api/v1/backups")
    def backups(mode: str = "demo"):
        return response(run(mode, lambda w: w.backups()))

    @app.post("/api/v1/backups")
    def backup(mode: str = "demo"):
        return response(run(mode, lambda w: w.create_backup()))

    @app.get("/api/v1/backups/{id}/download")
    def backup_download(id: str, mode: str = "demo"):
        path = run(mode, lambda w: w.backup_path(id) / "backup.zip")
        require(path.is_file(), "BACKUP_NOT_FOUND", "备份不存在")
        return FileResponse(path, filename=f"{mode}-{id}.zip")

    @app.post("/api/v1/restore/preview")
    def restore_preview(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.preview_restore(p["id"])))

    @app.post("/api/v1/restore/commit")
    def restore_commit(p: dict, mode: str = "demo"):
        return response(run(mode, lambda w: w.restore(p["id"], p["sha256"], p.get("confirm"))))

    @app.post("/api/v1/sync")
    def sync():
        sync_all()
        return response({"status": "LOCAL_CACHE_CHECKED", "message": "已检查交易自动入账、缓存与预测结果"})

    @app.get("/api/v1/export")
    def export(mode: str = "demo"):
        data = run(mode, lambda w: {"state": w.snapshot(), "predictions": w.prediction_list(), "exported_at": w.now()})
        result = response(data)
        result.headers["Content-Disposition"] = f'attachment; filename="funding-{mode}.json"'
        return result

    @app.get("/api/v1/templates/{kind}")
    def template(kind: str):
        names = {"observations": "nav_history.csv", "funds": "funds.csv", "lots": "lots.csv", "orders": "orders.csv",
                 "confirmations": "confirmations.csv", "cash_events": "cash_events.csv"}
        require(kind in names, "TEMPLATE_NOT_FOUND", "没有此模板")
        path = resources / "input/templates" / names[kind]
        require(path.is_file(), "TEMPLATE_NOT_FOUND", "模板文件不存在")
        return FileResponse(path, filename=names[kind], media_type="text/csv; charset=utf-8")

    dist = resources / "src/frontend/dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def index():
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-store"})
        return response({"message": "前端尚未构建，请先运行前端 build"}, 503)

    return app
