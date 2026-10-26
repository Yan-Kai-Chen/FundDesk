from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import time

from decimal import Decimal as D
from fastapi.testclient import TestClient

from app.domain.finance import Problem
from app.main import create_app
from app.persistence.store import Store
from conftest import opening


def test_snapshot_projects_current_price_impact_without_writing_or_using_pending_buys(wb):
    with wb.store.transaction() as c:
        f = Store.doc(c, "fund", wb.fid)
        Store.put(c, "fund", wb.fid, dict(f, fund_code="012183", share_class="C"), f["_version"])
    opening(wb)
    for value, date, kind in [("1", "2026-10-19", "OFFICIAL_NAV"), ("1.02", "2026-10-20", "THIRD_PARTY_ESTIMATE")]:
        wb.observe({"fund_id": wb.fid, "value_kind": kind, "effective_date": date, "value": value,
                    "as_of": "2026-10-20T05:29:00Z" if kind != "OFFICIAL_NAV" else None})
    wb.order({"fund_id": wb.fid, "side": "BUY", "bucket": "TACTICAL", "requested": "200",
              "paid_immediately": True, "occurred_at": "2026-10-20T05:30:00Z", "key": "pending-overview-buy"})
    with wb.store.connect() as c:
        before = {table: [tuple(r) for r in c.execute(f"SELECT * FROM {table}")] for table in ["events", "documents", "observations", "jobs", "plans"]}
    state = wb.snapshot(); f = state["funds"][0]
    assert f["day_valuation"]["price_change"] == D("20") and f["day_valuation"]["units"] == D("1000")
    assert state["portfolio"]["pending_buy"] == D("200")
    assert state["portfolio"]["day_valuation"]["total"] == D("20")
    assert f["market_value"] == D("1000") and state["portfolio"]["pnl"] == 0
    with wb.store.connect() as c:
        after = {table: [tuple(r) for r in c.execute(f"SELECT * FROM {table}")] for table in before}
    assert before == after and wb.snapshot() == state


def test_refresh_all_bounds_concurrency_and_preserves_independent_outcomes(wb, monkeypatch):
    ids = [wb.save_fund({"fund_code": code, "name": "合成全刷新", "share_class": "C"})["id"]
           for code in ["012183", "021528", "012738", "008987", "019633", "017954"]]
    active = peak = 0; lock = Lock(); calls = []
    def refresh(fid, *, manual=False):
        nonlocal active, peak
        assert manual
        with lock:
            active += 1; peak = max(peak, active); calls.append(fid)
        try:
            time.sleep(.02)
            if fid == ids[0]: raise Problem("SOURCE_DOWN", "合成源不可用")
            if fid == ids[1]: return {"fund_id": fid, "nav": {"status": "READY"}, "intraday": {"status": "WAIT"}}
            if fid == ids[2]: return {"fund_id": fid, "nav": {"status": "FAILED"}, "intraday": {"status": "READY"}}
            return {"fund_id": fid, "nav": {"status": "READY"}, "intraday": {"status": "OUTSIDE_SESSION"}}
        finally:
            with lock: active -= 1
    monkeypatch.setattr(wb, "refresh_market", refresh)
    first = wb.refresh_all_market()
    assert peak == 3 and len(calls) == 6
    assert first["total_count"] == 7 and first["completed_count"] == 3
    assert first["waiting_count"] == 1 and first["failed_count"] == 2 and first["unsupported_count"] == 1
    assert first["status"] == "PARTIAL"
    wb.refresh_all_market()
    assert len(calls) == 12  # Explicit repeats do not inherit the automatic ten-minute gate.


def test_refresh_all_endpoint_checks_session_mode_and_never_holds_ui_lock(tmp_path, monkeypatch):
    app = create_app(tmp_path, testing=True)
    w = app.state.workbenches["real"]
    w.save_fund({"fund_code": "012183", "name": "合成并发接口", "share_class": "C"})
    entered, release = Event(), Event()
    def slow(fid, *, manual=False):
        assert manual; entered.set(); assert release.wait(3)
        return {"fund_id": fid, "nav": {"status": "READY"}, "intraday": {"status": "READY"}}
    monkeypatch.setattr(w, "refresh_market", slow)
    with TestClient(app) as client:
        assert client.post("/api/v1/market/refresh-all?mode=real", json={}).status_code == 401
        client.headers["x-csrf-token"] = client.get("/api/v1/session").json()["csrf_token"]
        assert client.post("/api/v1/market/refresh-all?mode=demo", json={}).json()["code"] == "REAL_ONLY"
        assert client.post("/api/v1/market/refresh-all?mode=other", json={}).json()["code"] == "INVALID_MODE"
        with ThreadPoolExecutor(max_workers=2) as pool:
            request = pool.submit(client.post, "/api/v1/market/refresh-all?mode=real", json={})
            try:
                assert entered.wait(2)
                assert pool.submit(client.get, "/api/v1/state?mode=real").result(timeout=2).status_code == 200
            finally:
                release.set()
            response = request.result(timeout=2)
        assert response.status_code == 200 and response.json()["completed_count"] == 1
