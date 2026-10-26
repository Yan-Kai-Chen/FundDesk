"""Isolated forecast storage/time/provenance behavior, no real workspace mutation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal as D
from threading import Event
import copy

import pytest

from app.domain.finance import Problem
from app.persistence.store import Store, digest, pack
from app.services.forecasting import ForecastingMixin, ENGINE_VERSION, RUN_LIMIT
from app.services.workbench import Workbench


@pytest.fixture
def fw(tmp_path, monkeypatch):
    cls = Workbench if issubclass(Workbench, ForecastingMixin) else type("ForecastTestWorkbench", (ForecastingMixin, Workbench), {})
    w = cls(Store(tmp_path / "data/real/workbench.sqlite3", "real"), tmp_path)
    w.now = lambda: "2026-10-20T05:30:00+00:00"
    f = w.save_fund({"fund_code": "012738", "name": "Synthetic forecast fund", "share_class": "C"})
    w.fid = f["id"]
    w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-19", "value": "1", "source": "Synthetic verified disclosure", "quality": "DISTRIBUTOR_DISCLOSURE"})
    end = date(2026, 10, 19)
    rows = [[(end - timedelta(days=500 - i)).isoformat(), "1", str(D("100") + D(i) / 100)] for i in range(501)]
    save_series(w, rows)
    monkeypatch.setattr("app.services.forecasting.fit_forecast", fake_fit)
    return w


def save_series(w, rows, **extra):
    with w.store.transaction() as c:
        old = Store.doc(c, "research_series", "012738")
        Store.put(c, "research_series", "012738", {"id": "012738", "data": {"fund_code": "012738", "rows": rows,
            "series_kind": "FUND_TOTAL_RETURN", "corporate_actions_verified": True, "basis": "SYNTHETIC_ISOLATED_TEST",
            "observed_at": w.now(), "source": "Synthetic fixture", "actions": [], **extra}}, old["_version"] if old else 0)


def data_rows(w):
    with w.store.connect() as c:
        return copy.deepcopy(Store.doc(c, "research_series", "012738")["data"]["rows"])


def fake_fit(rows, horizon):
    return {"status": "READY", "forecast_return": D("0.01"), "lower_return": D("-0.02"), "upper_return": D("0.03"),
        "selected_model": "ZERO", "selection_reason": "Synthetic deterministic test", "validation": {"mae": D(".02")},
        "calibration": {"count": 100, "interval_width": D(".05")}, "test": {"count": 100, "mae": D(".02"), "baseline_mae": D(".03"), "improved_over_baseline": True},
        "candidates": [], "history": {"train": {"start": rows[0][0], "end": rows[-1][0], "count": len(rows)}},
        "folds": {"test": [{"date": rows[-1][0], "forecast_return": D(".01")}]}, "config": {"synthetic": True}}


def generate(w):
    return w.generate_forecasts({"refresh": False})


def doc_counts(w):
    with w.store.connect() as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("events", "observations", "documents", "predictions", "resolutions")}


def test_dashboard_is_readonly_and_demo_does_not_borrow_real(fw, tmp_path):
    before = doc_counts(fw)
    for _ in range(3):
        result = fw.forecasting_dashboard()
        assert result["status"] == "EMPTY" and result["latest"] is None
        assert result["maintenance"]["status"] == "DISABLED"
    assert doc_counts(fw) == before
    cls = type(fw)
    demo = cls(Store(tmp_path / "data/demo/workbench.sqlite3", "demo"), tmp_path)
    assert demo.forecasting_dashboard()["latest"] is None
    with pytest.raises(Problem, match="真实账户"):
        demo.generate_forecasts({"refresh": False})
    assert demo.forecast_maintenance()["status"] == "DISABLED"


def test_generation_freezes_input_and_keeps_financial_facts_unchanged(fw):
    original = fw.snapshot()
    run = generate(fw)
    assert run["status"] == "READY" and not run["stale"]
    one, five = run["funds"][0]["forecasts"]
    assert one["target_date"] == "2026-10-20" and five["target_date"] == "2026-10-26"
    assert one["task"] == "TOTAL_RETURN" and one["predicted_nav_reference"] == "1.01"
    assert one["generated_at"] == fw.now() and one["history"]["train"]["count"] == 501
    full = fw.forecast_run_export(run["id"])
    assert len(full["inputs"][fw.fid]["rows"]) == 501
    assert full["inputs"][fw.fid]["models"]["1"]["folds"]["test"]
    assert "folds" not in one and "candidates" not in one
    for field in ("revision", "portfolio", "orders", "lots", "audit"):
        assert original[field] == fw.snapshot()[field]
    with fw.store.connect() as c:
        assert not c.execute("SELECT 1 FROM predictions").fetchone()
        assert not c.execute("SELECT 1 FROM resolutions").fetchone()


def test_data_basis_idempotency_and_gets_never_write(fw):
    first = generate(fw)
    later = fw.generate_forecasts({"key": "different-transport-key", "refresh": False})
    assert later["duplicate"] is True and later["id"] == first["id"]
    before = doc_counts(fw)
    for _ in range(3):
        assert len(fw.forecasting_dashboard()["recent_predictions"]) == 2
        fw.forecast_run_export(first["id"])
    assert doc_counts(fw) == before
    with fw.store.connect() as c:
        assert len(Store.docs(c, "forecast_prediction")) == 2
        assert len(Store.docs(c, "forecast_run")) == 1


@pytest.mark.parametrize("bad", ["nav", "date", "newer", "quality", "actions"])
def test_mismatched_or_unverified_history_waits_without_fake_alignment(fw, bad):
    rows = data_rows(fw)
    if bad == "nav": rows[-1][1] = "1.1"
    if bad == "date": rows = rows[:-1]
    if bad == "newer": rows.append(["2026-10-20", "1", "106"])
    extra = {"corporate_actions_verified": False} if bad == "actions" else {}
    if bad == "quality":
        fw.observe({"fund_id": fw.fid, "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-19", "value": "1.01", "source": "Synthetic manual correction"})
        rows[-1][1] = "1.01"
    save_series(fw, rows, **extra)
    result = generate(fw)
    assert result["status"] == "WAIT"
    assert all(x["status"] == "WAIT" and not x.get("prediction_id") for x in result["funds"][0]["forecasts"])
    with fw.store.connect() as c:
        assert not Store.docs(c, "forecast_prediction")
        assert not Store.doc(c, "forecast_automation", "current")


def test_expired_horizon_waits_independently_and_same_day_existing_nav_never_forecasts(fw):
    fw.now = lambda: "2026-10-21T03:00:00+00:00"
    result = generate(fw)
    forecasts = result["funds"][0]["forecasts"]
    assert forecasts[0]["code"] == "BASE_EXPIRED"
    assert forecasts[1]["status"] == "READY"
    assert result["status"] == "PARTIAL"


def test_new_quote_or_ledger_does_not_stale_model_basis_but_formal_history_does(fw):
    result = generate(fw)
    fw.observe({"fund_id": fw.fid, "value_kind": "THIRD_PARTY_ESTIMATE", "effective_date": "2026-10-20", "value": "1.03", "source": "Synthetic intraday"})
    fw.event("OPENING_LOT", {"fund_id": fw.fid, "units": "100", "cost": "100", "acquired": "2026-09-01", "available_from": "2026-09-03"}, "forecast-open-lot-1", fw.now())
    latest = fw.forecasting_dashboard()["latest"]
    assert not latest["stale"]
    holding = latest["funds"][0]["forecasts"][0]["holdings_reference"]
    assert holding["units"] == D("100") and holding["forecast_change"] == D("1.00")
    rows = data_rows(fw); rows[-2][2] = "104.9"
    save_series(fw, rows)
    assert fw.forecasting_dashboard()["latest"]["stale"]
    assert fw.forecast_run_export(result["id"])["inputs"][fw.fid]["rows"][-2][2] != "104.9"


def actual(w, nav=".95", tri="106.05", quality="MANAGER_DISCLOSURE"):
    w.now = lambda: "2026-10-20T12:00:00+00:00"
    w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-20", "value": nav, "source": "Synthetic later disclosure", "quality": quality})
    rows = data_rows(w)
    save_series(w, rows + [["2026-10-20", nav, tri]], actions=[{"date": "2026-10-20", "cash_per_old_unit": ".06"}])


def resolve(w):
    with w.store.transaction() as c:
        return w._resolve_forecasts(c, w.snapshot(c), w.research_datasets(c))


def test_resolution_uses_total_return_not_nav_drawdown_and_is_immutable(fw):
    first = generate(fw)
    prediction = first["funds"][0]["forecasts"][0]
    actual(fw)
    assert resolve(fw) == 1
    resolution = fw.forecasting_dashboard()["recent_predictions"][0]["resolution"]
    if not resolution:
        resolution = fw.forecasting_dashboard()["recent_predictions"][1]["resolution"]
    assert _d(resolution["actual_return"]) == D(".01")  # 106.05/105-1 despite .95 NAV.
    assert _d(resolution["error_pp"]) == 0 and resolution["interval_covered"]
    assert _d(resolution["baseline_absolute_error_pp"]) == 1
    fw.now = lambda: "2026-10-20T13:00:00+00:00"
    fw.observe({"fund_id": fw.fid, "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-20", "value": "1.2", "source": "Synthetic subsequent revision", "quality": "MANAGER_DISCLOSURE"})
    assert resolve(fw) == 0
    with fw.store.connect() as c:
        saved = Store.doc(c, "forecast_resolution", prediction["id"])
        assert D(saved["actual_nav"]) == D(".95")
    score = next(s for s in fw.forecasting_dashboard()["scores"] if s["fund_id"] == fw.fid and s["horizon"] == 1)
    assert score["resolved"] == 1 and score["mae_pp"] == 0 and score["baseline_mae_pp"] == 1


def _d(value):
    return D(str(value))


@pytest.mark.parametrize("issue", ["manual", "baseline_revision", "tri_mismatch", "old_generated", "already_seen"])
def test_resolution_requires_formal_unseen_target_and_matching_reconstructed_data(fw, issue):
    run = generate(fw)
    pred = run["funds"][0]["forecasts"][0]
    actual(fw, quality="MANUAL" if issue == "manual" else "MANAGER_DISCLOSURE")
    if issue == "baseline_revision":
        fw.observe({"fund_id": fw.fid, "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-19", "value": "1.1", "source": "Synthetic baseline revision", "quality": "MANAGER_DISCLOSURE"})
    if issue == "tri_mismatch":
        rows = data_rows(fw); rows[-1][1] = "1.5"; save_series(fw, rows)
    if issue in {"old_generated", "already_seen"}:
        with fw.store.transaction() as c:
            saved = Store.doc(c, "forecast_prediction", pred["id"])
            if issue == "old_generated": saved["generated_at"] = "2026-10-21T03:00:00+00:00"
            else: saved["target_existed_at_generation"] = True
            # Deliberate fixture corruption is confined to temporary test DB.
            Store.put(c, "forecast_prediction", saved["id"], saved, saved["_version"])
    assert resolve(fw) == 0


def test_refresh_and_rolling_fit_never_hold_write_or_page_locks(fw, monkeypatch):
    entered, release = Event(), Event()
    def slow(rows, horizon):
        entered.set()
        assert release.wait(3)
        return fake_fit(rows, horizon)
    monkeypatch.setattr("app.services.forecasting.fit_forecast", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(generate, fw)
        try:
            assert entered.wait(1)
            assert pool.submit(fw.forecasting_dashboard).result(timeout=1)["latest"] is None
            with pytest.raises(Problem, match="正在生成"):
                generate(fw)
            # Independent non-financial write is not blocked by the computation.
            with fw.store.transaction() as c:
                Store.put(c, "synthetic_setting", "test", {"value": True}, 0)
        finally:
            release.set()
        assert future.result(timeout=2)["status"] == "READY"


def test_final_commit_rechecks_only_model_basis(fw, monkeypatch):
    def revise(rows, horizon):
        if horizon == 1:
            modified = data_rows(fw); modified[0][2] = "99"
            save_series(fw, modified)
        return fake_fit(rows, horizon)
    monkeypatch.setattr("app.services.forecasting.fit_forecast", revise)
    with pytest.raises(Problem, match="正式净值或研究历史"):
        generate(fw)
    with fw.store.connect() as c:
        assert not Store.docs(c, "forecast_prediction") and not Store.docs(c, "forecast_run")


def test_runs_bounded_predictions_permanent_and_same_data_same_prediction(fw):
    first = generate(fw)
    first_id = first["funds"][0]["forecasts"][0]["id"]
    for i in range(RUN_LIMIT + 1):
        rows = data_rows(fw); rows[0][2] = str(D("99") + D(i) / 100)
        save_series(fw, rows)
        generate(fw)
    dashboard = fw.forecasting_dashboard()
    assert len(dashboard["history"]) == RUN_LIMIT
    with fw.store.connect() as c:
        assert Store.doc(c, "forecast_prediction", first_id)
        assert len(Store.docs(c, "forecast_prediction")) == 2 * (RUN_LIMIT + 2)
    with pytest.raises(Problem, match="回收"):
        fw.forecast_run_export(first["id"])


def test_maintenance_cadence_no_repeated_fit_and_retries_backoff(fw, monkeypatch):
    assert fw.forecast_maintenance()["status"] == "DISABLED"
    generate(fw)
    calls = []
    def nofit(rows, horizon):
        calls.append(horizon)
        return fake_fit(rows, horizon)
    monkeypatch.setattr("app.services.forecasting.fit_forecast", nofit)
    first = fw.forecast_maintenance()
    assert first["status"] == "READY" and not calls
    before = doc_counts(fw)
    assert fw.forecast_maintenance() == first and doc_counts(fw) == before
    fw.now = lambda: "2026-10-20T05:41:00+00:00"
    rows = data_rows(fw); rows[0][2] = "99"; save_series(fw, rows)
    assert fw.forecast_maintenance()["status"] == "READY" and calls == [1, 5]
    fw.now = lambda: "2026-10-20T05:52:00+00:00"
    rows = data_rows(fw); rows[0][2] = "98"; save_series(fw, rows)
    def fail(*args): raise Problem("SYNTHETIC_FAILURE", "Synthetic fit unavailable")
    monkeypatch.setattr("app.services.forecasting.fit_forecast", fail)
    failed = fw.forecast_maintenance()
    assert failed["status"] == "WAIT" and failed["code"] == "SYNTHETIC_FAILURE"
    assert stamp_delta(failed["next_check_at"], fw.now()) == 1800


def stamp_delta(a, b):
    from app.domain.finance import stamp
    return (stamp(a) - stamp(b)).total_seconds()


def test_default_explicit_refresh_and_future_observation_time_guard(fw, monkeypatch):
    calls = []
    monkeypatch.setattr(fw, "refresh_research_data", lambda: calls.append(True) or {"status": "READY"})
    assert fw.generate_forecasts({})["refresh_status"]["status"] == "READY"
    assert calls == [True]
    actual(fw)
    rows = data_rows(fw)
    save_series(fw, rows, observed_at="2026-10-21T05:00:00+00:00")
    assert resolve(fw) == 0


def test_missing_interval_is_unknown_in_holdings_resolution_and_coverage(fw, monkeypatch):
    def no_interval(rows, horizon):
        result = fake_fit(rows, horizon)
        result.update(lower_return=None, upper_return=None)
        return result
    monkeypatch.setattr("app.services.forecasting.fit_forecast", no_interval)
    run = generate(fw)
    forecast = run["funds"][0]["forecasts"][0]
    assert forecast["holdings_reference"]["lower_change"] is None
    assert forecast["holdings_reference"]["upper_change"] is None
    actual(fw)
    assert resolve(fw) == 1
    score = next(s for s in fw.forecasting_dashboard()["scores"] if s["fund_id"] == fw.fid and s["horizon"] == 1)
    assert score["interval_count"] == 0 and score["interval_coverage"] is None


def test_real_engine_window_metadata_and_permanent_configuration(fw, monkeypatch):
    from app.domain.forecasting import fit_forecast
    rows = data_rows(fw)
    end = date.fromisoformat(rows[0][0])
    prefix = [[(end - timedelta(days=510 - i)).isoformat(), "1", str(D("94.9") + D(i) / 100)] for i in range(510)]
    save_series(fw, prefix + rows)
    monkeypatch.setattr("app.services.forecasting.fit_forecast", fit_forecast)
    run = generate(fw)
    one = run["funds"][0]["forecasts"][0]
    assert one["status"] == "READY" and one["history"]["count"] == 1000
    assert one["history"]["source_count"] == 1011
    assert one["history"]["start"] > one["history"]["source_start"]
    with fw.store.connect() as c:
        saved = Store.doc(c, "forecast_prediction", one["id"])
        assert saved["model_config"]["max_points"] == 1000
    assert "model_config" not in one


def test_forecast_api_session_mode_and_readonly_export(fw, monkeypatch):
    from app.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(fw.root, testing=True)
    # create_app constructs independent Workbenches for this same temporary DB.
    monkeypatch.setattr(Workbench, "now", lambda w: fw.now())
    with TestClient(app) as client:
        assert client.get("/api/v1/forecasting?mode=real").status_code == 401
        csrf = client.get("/api/v1/session").json()["csrf_token"]
        client.headers["x-csrf-token"] = csrf
        assert client.get("/api/v1/forecasting?mode=demo").json()["status"] == "EMPTY"
        assert client.post("/api/v1/forecasting/runs?mode=demo", json={"refresh": False}).status_code == 400
        assert client.post("/api/v1/forecasting/runs?mode=invalid", json={"refresh": False}).status_code == 400
        response = client.post("/api/v1/forecasting/runs?mode=real", json={"refresh": False})
        assert response.status_code == 200, response.text
        run = response.json()
        assert client.get("/api/v1/forecasting?mode=real").json()["latest"]["id"] == run["id"]
        before = doc_counts(fw)
        exported = client.get(f'/api/v1/forecasting/runs/{run["id"]}/export?mode=real')
        assert exported.status_code == 200 and exported.json()["inputs"][fw.fid]["models"]["1"]["folds"]
        assert doc_counts(fw) == before
