"""V2 profiles and derived references never change local financial facts."""
from decimal import Decimal as D

from fastapi.testclient import TestClient
import pytest

from app.domain.finance import Problem
from app.domain.research_calendar import reference_sessions
from app.main import create_app
from app.persistence.store import Store
from app.services.workbench import Workbench
from conftest import event, opening


def profile(version=0, template="DRAWDOWN_GUARD_V2", enabled=True, cap="6000"):
    return {"enabled": enabled, "holding_cap": cap, "template": template, "expected_version": version}


def rows(w):
    with w.store.connect() as c:
        return {table: [tuple(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("events", "documents", "observations", "plans")}


def seed(w, history=80):
    with w.store.transaction() as c:
        fund = Store.doc(c, "fund", w.fid)
        Store.put(c, "fund", w.fid, dict(fund, fund_code="012738", share_class="C"), fund["_version"])
    opening(w, units="1000", cost="1000")
    event(w, "OPENING_LOT", {"fund_id": w.fid, "lot_id": "v2-synthetic-t-lot", "units": "500",
          "cost": "450", "acquired": "2026-06-01", "available_from": "2026-06-03", "bucket": "TACTICAL"})
    dates = [d for d in reference_sessions() if d <= "2026-10-19"][-history:]
    for i, on in enumerate(dates):
        w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "effective_date": on,
                   "value": str(D("1") + D(i) / 10000), "source": "Synthetic V2 history only"})
    previous = D("1") + D(len(dates) - 1) / 10000
    w.observe({"fund_id": w.fid, "value_kind": "THIRD_PARTY_ESTIMATE", "effective_date": "2026-10-20",
               "value": str(previous * D("0.98")), "as_of": "2026-10-20T05:29:00Z",
               "source": "Synthetic V2 intraday quote only"})
    return dates


def test_v2_profile_keeps_v1_other_fund_and_all_financial_facts(wb):
    seed(wb)
    wb.save_investment_plan(wb.fid, profile(template="DAILY_LADDER_V1", cap="4000"))
    other = wb.save_fund({"fund_code": "019633", "share_class": "C", "name": "Independent synthetic V1 fund"})
    wb.save_investment_plan(other["id"], profile(template="DAILY_LADDER_V1", enabled=False, cap="4000"))
    before = wb.snapshot()
    saved = wb.save_investment_plan(wb.fid, profile(version=1))
    assert saved["template"] == "DRAWDOWN_GUARD_V2" and saved["holding_cap"] == "6000.00"
    assert saved["_version"] == 2
    after = wb.snapshot()
    assert before["portfolio"]["cash"] == 0
    assert (after["revision"], after["orders"], after["lots"], after["portfolio"]) == (
        before["revision"], before["orders"], before["lots"], before["portfolio"])
    a, b = ({f["id"]: f for f in snapshot["funds"]} for snapshot in (before, after))
    assert a[wb.fid]["position_buckets"] == b[wb.fid]["position_buckets"]
    assert a[other["id"]]["investment_plan_profile"] == b[other["id"]]["investment_plan_profile"]
    restarted = Workbench(Store(wb.store.path, "real"), wb.root)
    restarted.now = wb.now
    assert next(f for f in restarted.snapshot()["funds"] if f["id"] == wb.fid)["investment_plan_profile"] == saved
    before_idempotent = rows(wb)
    assert wb.save_investment_plan(wb.fid, profile(version=2)) == saved
    assert rows(wb) == before_idempotent
    with pytest.raises(Problem) as exc:
        wb.save_investment_plan(wb.fid, profile(version=1))
    assert exc.value.code == "VERSION_CONFLICT"
    assert rows(wb) == before_idempotent


def test_v2_initialization_checks_remain_and_failed_save_is_atomic(wb):
    with wb.store.transaction() as c:
        fund = Store.doc(c, "fund", wb.fid)
        Store.put(c, "fund", wb.fid, dict(fund, fund_code="012738", share_class="C"), fund["_version"])
    opening(wb, cost=None)
    wb.observe({"fund_id": wb.fid, "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-19", "value": "1"})
    before = rows(wb)
    with pytest.raises(Problem) as exc:
        wb.save_investment_plan(wb.fid, profile())
    assert exc.value.code == "HOLDINGS_UNKNOWN"
    assert rows(wb) == before


def test_v2_insufficient_history_is_explicit_read_only_and_does_not_block_manual_records(wb):
    seed(wb, history=20)
    wb.save_investment_plan(wb.fid, profile())
    before = rows(wb)
    current = wb.recommendations(persist=False)
    item = current["daily_t_items"][0]
    assert item["status"] == "RISK_HISTORY_MISSING"
    assert item["can_apply"] is False
    assert item["plan"]["risk"]["ready"] is False
    assert rows(wb) == before
    result = wb.order({"fund_id": wb.fid, "side": "BUY", "requested": "100",
                       "bucket": "TACTICAL", "occurred_at": wb.now(),
                       "key": "synthetic-v2-manual-buy", "paid_immediately": True})
    after = wb.snapshot()
    own = next(order for order in after["orders"] if order["id"] == result["order_id"])
    assert own["requested"] == D("100") and own["cash_source"] == "EXTERNAL"
    assert own["paid"] == D("100")
    assert after["portfolio"]["cash"] == 0


def test_v2_historical_correction_invalidates_same_minute_cached_reference(wb):
    dates = seed(wb)
    wb.save_investment_plan(wb.fid, profile())
    previous = wb.recommendations()
    latest_id = previous["snapshot"]["funds"][0][1]
    ledger_revision = previous["ledger_version"]
    wb.observe({"fund_id": wb.fid, "value_kind": "OFFICIAL_NAV", "effective_date": dates[-10],
                "value": "1.8", "source": "Synthetic correction to older formal history"})
    changed = wb.recommendations(persist=False)
    assert changed["id"] != previous["id"]
    assert changed["snapshot"]["funds"][0][1] == latest_id
    assert changed["ledger_version"] == ledger_revision
    assert changed["snapshot"]["evaluation_minute"] == previous["snapshot"]["evaluation_minute"]
    assert changed["snapshot"]["investment_plan_risk_history"] != previous["snapshot"]["investment_plan_risk_history"]
    before_read = rows(wb)
    assert wb.recommendations(persist=False)["id"] == changed["id"]
    assert rows(wb) == before_read


def test_v2_same_source_receipt_does_not_invalidate_current_reference(wb):
    dates = seed(wb)
    wb.save_investment_plan(wb.fid, profile())
    previous = wb.recommendations()
    wb.now = lambda: "2026-10-20T05:30:30+00:00"
    receipt = wb.observe({"fund_id": wb.fid, "value_kind": "OFFICIAL_NAV", "effective_date": dates[-10],
                          "value": str(D("1") + D(len(dates) - 10) / 10000),
                          "source": "Synthetic V2 history only"})
    assert receipt["duplicate"] is True
    assert wb.recommendations(persist=False)["id"] == previous["id"]


def test_v2_api_retains_auth_csrf_account_isolation_and_read_only_projection(wb):
    seed(wb)
    app = create_app(wb.root, testing=True)
    app.state.workbenches["real"].now = wb.now
    path = f"/api/v1/funds/{wb.fid}/investment-plan"
    with TestClient(app) as client:
        assert client.put(path + "?mode=real", json=profile()).status_code == 401
        csrf = client.get("/api/v1/session").json()["csrf_token"]
        assert client.put(path + "?mode=real", json=profile()).status_code == 403
        client.headers["x-csrf-token"] = csrf
        assert client.put(path + "?mode=demo", json=profile()).json()["code"] == "FUND_NOT_FOUND"
        saved = client.put(path + "?mode=real", json=profile())
        assert saved.status_code == 200 and saved.json()["template"] == "DRAWDOWN_GUARD_V2"
        assert client.put(path + "?mode=real", json=profile()).json()["code"] == "VERSION_CONFLICT"
        before = rows(wb)
        current = client.get("/api/v1/recommendations/current?mode=real")
        assert current.status_code == 200
        assert current.json()["daily_t_items"][0]["plan"]["template"] == "DRAWDOWN_GUARD_V2"
        assert rows(wb) == before
        demo = client.get("/api/v1/state?mode=demo").json()
        assert all(not f["investment_plan_profile"]["enabled"] for f in demo["funds"])


@pytest.mark.parametrize("template", [[], {}, None, "OTHER"])
def test_v2_invalid_template_rejected_without_writes(wb, template):
    seed(wb, history=1)
    before = rows(wb)
    with pytest.raises(Problem) as exc:
        wb.save_investment_plan(wb.fid, profile(template=template))
    assert exc.value.code == "INVALID_INVESTMENT_PLAN"
    assert rows(wb) == before
