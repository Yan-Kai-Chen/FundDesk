from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal as D
import gzip
import json
from pathlib import Path
from threading import Event
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.domain.finance import Problem
from app.main import create_app
from app.persistence.store import Store
from app.providers.research_history import PRODUCTS
from app.services.research import ASSETS
from conftest import event


def nav_history(w):
    start = date(2026, 10, 19) - timedelta(days=24)
    for i in range(25):
        w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "effective_date": (start + timedelta(days=i)).isoformat(),
                   "value": str(D("1") + D(i) / 1000), "source": "Synthetic research fixture"})


def test_readonly_pages_do_not_generate_predictions_plans_or_research(wb):
    with wb.store.connect() as c:
        before = {table: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ["plans", "predictions", "events", "documents"]}
    for _ in range(3):
        assert wb.research_dashboard()["latest"] is None
        wb.recommendations(persist=False)
    with wb.store.connect() as c:
        after = {table: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in before}
    assert after == before


def test_run_idempotency_forecast_resolution_staleness_and_no_financial_events(wb):
    nav_history(wb)
    initial = wb.snapshot()
    result = wb.generate_research({"key": "analysis-request-0001", "methods": ["TREND", "VALUE", "REBALANCE"]})
    assert result["forecasts"][0]["status"] == "READY"
    assert result["engine_result"] is None and result["execution_enabled"] is False
    same = wb.generate_research({"key": "analysis-request-0001", "methods": ["TREND", "VALUE", "REBALANCE"]})
    assert same["id"] == result["id"] and len(wb.prediction_list()) == 1
    wb.generate_research({"key": "analysis-request-0002", "methods": ["TREND"]})
    assert len(wb.prediction_list()) == 1
    with pytest.raises(Problem, match="同一请求"):
        wb.generate_research({"key": "analysis-request-0001", "methods": ["VALUE"]})
    for name in ["revision", "portfolio", "orders", "lots", "audit"]:
        assert initial[name] == wb.snapshot()[name]
    wb.now = lambda: "2026-10-20T10:00:00+00:00"
    wb.observe({"fund_id": wb.fid, "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-20", "value": "1.04", "source": "Synthetic later disclosure"})
    assert wb.research_get_run(result["id"])["stale"] is True
    score = wb.research_dashboard()["forecast_scores"][wb.fid]
    assert score["resolved"] == 1 and score["pending"] == 0
    assert score["mae"] == D(".016")
    exported = wb.research_run_export(result["id"])
    assert exported["input_context"]["snapshot"]["now"] == initial["now"]


def test_bounded_runs_keep_the_newest_even_with_a_fixed_clock_and_retain_forecasts(wb):
    nav_history(wb)
    last = None
    for i in range(32):
        last = wb.generate_research({"key": "retention-analysis-" + str(i), "methods": ["TREND"]})
    dashboard = wb.research_dashboard()
    assert len(dashboard["history"]) == 30 and dashboard["latest"]["id"] == last["id"]
    assert len(wb.prediction_list()) == 1


def test_policy_has_no_wallet_requirement_and_percentages_are_exact(wb):
    result = wb.save_research_policy({"level": "BALANCED", "target_percentages": {wb.fid: "33.33"}, "cash_budget": None, "expected_version": 0})
    assert D(result["target_weights"][wb.fid]) == D(".3333")
    assert result["cash_budget"] is None and wb.snapshot()["portfolio"]["cash"] == 0
    with pytest.raises(Problem):
        wb.save_research_policy({"level": "ACTIVE", "target_percentages": {wb.fid: "100.01"}, "expected_version": 1})
    with pytest.raises(Problem):
        wb.save_research_policy({"level": "ACTIVE", "target_weights": {"missing": "0.5"}, "expected_version": 1})
    with pytest.raises(Problem):
        wb.save_research_policy({"level": "ACTIVE", "target_weights": {wb.fid: 0.5}, "expected_version": 1})


def test_generation_refreshes_missing_known_history_once_and_freezes_the_updated_basis(wb, monkeypatch):
    f = wb.save_fund({"fund_code": "012738", "name": "Synthetic auto-refresh fixture", "share_class": "C"})
    wb.observe({"fund_id": f["id"], "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-19", "value": "1", "source": "Synthetic latest mark"})
    calls = []
    def fill():
        calls.append(True)
        with wb.store.transaction() as c:
            for id, rows in [("012738", [["2026-10-19", "1", "100"]]), ("cash", [["2026-10-19", "100"]])]:
                Store.put(c, "research_series", id, {"id": id, "data": {"rows": rows, "observed_at": wb.now()}}, 0)
    monkeypatch.setattr(wb, "refresh_research_data", fill)
    result = wb.generate_research({"key": "auto-collect-analysis-first", "methods": ["TREND"]})
    assert len(calls) == 1 and not result["stale"]
    wb.generate_research({"key": "auto-collect-analysis-second", "methods": ["TREND"]})
    assert len(calls) == 1


def real_import_fixture(w):
    # All numbers and VERIFIED claims here are synthetic test evidence in an isolated DB.
    raw = json.loads(gzip.decompress((ASSETS / "examples/synthetic_snapshot.json.gz").read_bytes()))
    w.now = lambda: raw["as_of"]
    mapping = {}
    for asset, code in zip(raw["instruments"], ["012738", "019633", "008987"]):
        f = w.save_fund({"fund_code": code, "name": "Synthetic imported model fixture " + code, "share_class": "C"})
        mapping[asset["id"]] = f["id"]
        asset["id"] = f["id"]
        asset["kind"], asset["market_scope"], asset["exposure_id"] = PRODUCTS[code]
    for holding in raw["portfolio"]["holdings"]:
        holding["asset_id"] = mapping[holding["asset_id"]]
        units = sum((D(lot["units"]) for lot in holding["lots"]), D("0"))
        event(w, "OPENING_LOT", {"fund_id": holding["asset_id"], "units": str(units), "cost": "1000", "bucket": "TACTICAL",
            "acquired": "2025-01-02", "available_from": "2025-01-03"}, at=w.now())
        w.observe({"fund_id": holding["asset_id"], "value_kind": "OFFICIAL_NAV", "effective_date": raw["decision_session"],
            "value": holding["mark"]["value"], "source": "Synthetic declared disclosure"})
    raw["policy"]["target_weights"] = {mapping[k]: v for k, v in raw["policy"]["target_weights"].items()}
    raw["policy"]["authority"] = "USER_CONFIGURED"
    raw["mode"] = "RESEARCH"
    for source in raw["sources"]: source["kind"] = "VERIFIED"
    return raw


def test_real_snapshot_import_matches_identity_holdings_and_runs_full_engine(tmp_path):
    from app.services.workbench import Workbench
    w = Workbench(Store(tmp_path / "data/real/workbench.sqlite3", "real"), tmp_path)
    raw = real_import_fixture(w)
    initial = w.snapshot()
    preview = w.import_research_input({"snapshot": raw}, preview=True)
    w.import_research_input({"snapshot": raw, "preview_hash": preview["raw_hash"], "expected_version": 0})
    w.save_research_policy({"level": "BALANCED", "target_weights": raw["policy"]["target_weights"],
                           "cash_budget": raw["portfolio"]["cash_settled"], "expected_version": 0})
    result = w.generate_research({"key": "isolated-real-methods-fixture", "methods": ["TREND", "VALUE", "REBALANCE"]})
    assert result["engine_result"] is not None
    assert set(result["engine_result"]["methods"]) == {"TREND", "VALUE", "REBALANCE"}
    assert result["engine_result"]["execution_enabled"] is False
    assert D(w.research_run_export(result["id"])["input"]["portfolio"]["cash_settled"]) == D(raw["portfolio"]["cash_settled"])
    assert w.snapshot()["revision"] == initial["revision"]
    for field in ["portfolio", "lots", "orders"]: assert w.snapshot()[field] == initial[field]
    wrong = deepcopy(raw); wrong["instruments"][0]["market_scope"] = "CN_HK"
    with pytest.raises(Problem, match="分类"):
        w.import_research_input({"snapshot": wrong}, preview=True)
    wrong = deepcopy(raw); wrong["portfolio"]["holdings"][0]["lots"][0]["units"] = "1"
    with pytest.raises(Problem, match="份额"):
        w.import_research_input({"snapshot": wrong}, preview=True)


def test_api_session_mode_synthetic_rejection_and_live_collection_outside_request_lock(tmp_path, monkeypatch):
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as client:
        assert client.post('/api/v1/research/runs?mode=real',json={}).status_code == 401
        csrf = client.get('/api/v1/session').json()['csrf_token']
        client.headers['x-csrf-token'] = csrf
        assert client.post('/api/v1/research/runs?mode=real',json={'methods':['BAD'],'key':'abcdefgh'}).status_code == 400
        raw = json.loads(gzip.decompress((ASSETS / 'examples/synthetic_snapshot.json.gz').read_bytes()))
        assert client.post('/api/v1/research/snapshots/preview?mode=real',json={'snapshot':raw}).status_code == 400
        demo = client.post('/api/v1/research/runs?mode=demo',json={'methods':['TREND','VALUE','REBALANCE'],'key':'demo-integration-full'}).json()
        assert demo['engine_result']['execution_enabled'] is False and demo['data_basis'] == 'SYNTHETIC_FUNCTIONAL_ONLY'
        assert client.get('/api/v1/research?mode=real').json()['latest'] is None
        entered, release = Event(), Event()
        def slow(code):
            entered.set(); assert release.wait(3)
            raise Problem('SOURCE_UNAVAILABLE','Synthetic outage')
        monkeypatch.setattr('app.services.research.fetch_body', slow)
        with ThreadPoolExecutor(max_workers=2) as pool:
            refresh = pool.submit(client.post,'/api/v1/research/data/refresh?mode=real',json={})
            try:
                assert entered.wait(1)
                assert pool.submit(client.get,'/api/v1/state?mode=real').result(timeout=1).status_code == 200
            finally:
                release.set()
            assert refresh.result(timeout=2).json()['status'] == 'PARTIAL'
