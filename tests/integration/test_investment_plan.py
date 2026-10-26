from decimal import Decimal as D
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.domain.finance import Problem
from app.main import create_app
from app.persistence.store import Store
from app.services.workbench import Workbench
from conftest import event, opening


def payload(version=0, cap="4000.00", enabled=True):
    return {"enabled": enabled, "holding_cap": cap, "template": "DAILY_LADDER_V1", "expected_version": version}


def seed_plan(w):
    with w.store.transaction() as c:
        fund = Store.doc(c, "fund", w.fid)
        Store.put(c, "fund", w.fid, dict(fund, fund_code="019633", share_class="C"), fund["_version"])
    opening(w, "1000", "1000")
    event(w, "OPENING_LOT", {"fund_id": w.fid, "lot_id": "plan-t-lot", "units": "500", "cost": "450",
                            "acquired": "2026-06-01", "available_from": "2026-06-03", "bucket": "TACTICAL"})
    w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "value": "1", "effective_date": "2026-10-19"})
    w.observe({"fund_id": w.fid, "value_kind": "THIRD_PARTY_ESTIMATE", "value": "1.02",
               "effective_date": "2026-10-20", "as_of": "2026-10-20T05:29:00Z", "source": "Synthetic plan quote"})


def database_rows(w):
    with w.store.connect() as c:
        return {table: [tuple(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("events", "documents", "plans", "observations")}


def test_plan_profile_defaults_do_not_write_documents_or_enable_other_funds(wb):
    seed_plan(wb)
    second = wb.save_fund({"fund_code": "012738", "name": "第二只合成测试基金", "share_class": "C"})
    before = database_rows(wb)
    snapshot = wb.snapshot()
    assert all(f["investment_plan_profile"] == {"enabled": False, "holding_cap": None,
        "template": "DAILY_LADDER_V1", "_version": 0} for f in snapshot["funds"])
    assert database_rows(wb) == before
    wb.save_investment_plan(wb.fid, payload())
    funds = {f["id"]: f for f in wb.snapshot()["funds"]}
    assert funds[wb.fid]["investment_plan_profile"]["enabled"] is True
    assert funds[second["id"]]["investment_plan_profile"]["enabled"] is False


def test_saved_plan_persists_without_cash_constraints_or_changing_ledger_or_old_intensity(wb):
    seed_plan(wb)
    wb.save_t_profile(wb.fid, {"level": "BALANCED", "expected_version": 0})
    before = wb.snapshot()
    assert before["portfolio"]["cash"] == 0
    saved = wb.save_investment_plan(wb.fid, payload(cap="4000"))
    assert saved["holding_cap"] == "4000.00" and saved["_version"] == 1
    after = wb.snapshot()
    assert after["revision"] == before["revision"]
    assert after["portfolio"] == before["portfolio"]
    assert after["orders"] == before["orders"] and after["lots"] == before["lots"]
    assert after["funds"][0]["daily_t"]["profile"] == before["funds"][0]["daily_t"]["profile"]
    assert after["funds"][0]["position_buckets"] == before["funds"][0]["position_buckets"]
    restarted = Workbench(Store(wb.store.path, "real"), wb.root)
    restarted.now = wb.now
    assert restarted.snapshot()["funds"][0]["investment_plan_profile"] == saved


@pytest.mark.parametrize("change", [
    {"enabled": "true"}, {"enabled": 1}, {"holding_cap": 4000.5}, {"holding_cap": True},
    {"holding_cap": "NaN"}, {"holding_cap": "Infinity"}, {"holding_cap": "0"},
    {"holding_cap": "-1"}, {"holding_cap": "4000.001"}, {"holding_cap": "100000000.01"},
    {"holding_cap": None}, {"template": "OTHER"}, {"expected_version": True},
    {"expected_version": -1}, {"expected_version": "0"}, {"extra": "unaccepted"},
])
def test_invalid_plan_profiles_do_not_write(wb, change):
    seed_plan(wb)
    before = database_rows(wb)
    with pytest.raises(Problem):
        wb.save_investment_plan(wb.fid, dict(payload(), **change))
    assert database_rows(wb) == before


def test_missing_fields_and_stale_idempotent_request_are_rejected(wb):
    seed_plan(wb)
    incomplete = payload()
    incomplete.pop("expected_version")
    with pytest.raises(Problem):
        wb.save_investment_plan(wb.fid, incomplete)
    saved = wb.save_investment_plan(wb.fid, payload())
    before = database_rows(wb)
    assert wb.save_investment_plan(wb.fid, payload(version=1, cap="4000")) == saved
    assert database_rows(wb) == before
    with pytest.raises(Problem) as exc:
        wb.save_investment_plan(wb.fid, payload(version=0))
    assert exc.value.code == "VERSION_CONFLICT"
    assert database_rows(wb) == before


def test_plan_history_is_bounded_per_fund_and_only_changes_on_save(wb):
    seed_plan(wb)
    second = wb.save_fund({"fund_code": "012738", "name": "合成历史隔离基金", "share_class": "C"})
    wb.save_investment_plan(second["id"], payload(enabled=False))
    for index in range(24):
        saved = wb.save_investment_plan(wb.fid, payload(version=index, cap=str(4000 + index)))
    assert saved["_version"] == 24
    with wb.store.connect() as c:
        histories = Store.docs(c, "investment_plan_profile_history")
    own = [row for row in histories if row["fund_id"] == wb.fid]
    assert len(own) == 20
    assert sorted(row["profile_version"] for row in own) == list(range(5, 25))
    assert len([row for row in histories if row["fund_id"] == second["id"]]) == 1
    before = database_rows(wb)
    for _ in range(2):
        wb.snapshot()
        wb.recommendations(persist=False)
    assert database_rows(wb) == before


@pytest.mark.parametrize("failure", ["unsupported", "share_class", "no_holdings", "unknown_cost", "no_nav"])
def test_enable_requires_supported_and_reconciled_holdings(wb, failure):
    if failure not in {"no_holdings", "unknown_cost", "no_nav"}:
        seed_plan(wb)
        with wb.store.transaction() as c:
            fund = Store.doc(c, "fund", wb.fid)
            field = {"fund_code": "000002"} if failure == "unsupported" else {"share_class": "A"}
            Store.put(c, "fund", wb.fid, dict(fund, **field), fund["_version"])
    else:
        with wb.store.transaction() as c:
            fund = Store.doc(c, "fund", wb.fid)
            Store.put(c, "fund", wb.fid, dict(fund, fund_code="019633", share_class="C"), fund["_version"])
        if failure != "no_holdings":
            opening(wb, cost=None if failure == "unknown_cost" else "1000")
        if failure != "no_nav":
            wb.observe({"fund_id": wb.fid, "value_kind": "OFFICIAL_NAV", "value": "1", "effective_date": "2026-10-19"})
    before = database_rows(wb)
    with pytest.raises(Problem) as exc:
        wb.save_investment_plan(wb.fid, payload())
    assert exc.value.code == ("PLAN_PRODUCT_UNSUPPORTED" if failure in {"unsupported", "share_class"} else "HOLDINGS_UNKNOWN")
    assert database_rows(wb) == before


def test_plan_override_invalidates_same_minute_cached_reference_and_disable_restores_old_intensity(wb, monkeypatch):
    from app.domain import investment_plan as plan_domain
    seed_plan(wb)
    wb.save_t_profile(wb.fid, {"level": "BALANCED", "expected_version": 0})
    legacy = wb.recommendations()
    assert legacy["daily_t_items"][0]["level"] == "BALANCED"
    wb.save_investment_plan(wb.fid, payload())
    active = wb.recommendations()
    assert active["id"] != legacy["id"]
    assert active["daily_t_items"][0]["level"] == "PLAN"
    assert active["daily_t_items"][0]["profile_version"] == 1
    assert wb.recommendations()["id"] == active["id"]
    wb.save_investment_plan(wb.fid, payload(version=1, cap="5000"))
    changed = wb.recommendations()
    assert changed["id"] != active["id"]
    assert changed["daily_t_items"][0]["profile_version"] == 2
    monkeypatch.setattr(plan_domain, "METHOD_VERSION", "synthetic-plan-method-change")
    assert wb.recommendations()["id"] != changed["id"]
    wb.save_investment_plan(wb.fid, payload(version=2, cap="5000", enabled=False))
    inactive = wb.recommendations()
    assert inactive["daily_t_items"][0]["level"] == "BALANCED"
    assert inactive["daily_t_items"][0]["profile_version"] == 1


def test_plan_api_authentication_csrf_account_isolation_and_reads_are_read_only(wb):
    seed_plan(wb)
    app = create_app(wb.root, testing=True)
    app.state.workbenches["real"].now = wb.now
    path = f"/api/v1/funds/{wb.fid}/investment-plan"
    with TestClient(app) as client:
        assert client.put(path + "?mode=real", json=payload()).status_code == 401
        csrf = client.get("/api/v1/session").json()["csrf_token"]
        assert client.put(path + "?mode=real", json=payload()).status_code == 403
        client.headers["x-csrf-token"] = csrf
        assert client.put(path + "?mode=invalid", json=payload()).json()["code"] == "INVALID_MODE"
        assert client.put(path + "?mode=demo", json=payload()).json()["code"] == "FUND_NOT_FOUND"
        saved = client.put(path + "?mode=real", json=payload())
        assert saved.status_code == 200 and saved.json()["_version"] == 1
        assert client.put(path + "?mode=real", json=payload()).json()["code"] == "VERSION_CONFLICT"
        before = database_rows(wb)
        snapshot = client.get("/api/v1/state?mode=real").json()
        current = client.get("/api/v1/recommendations/current?mode=real").json()
        assert snapshot["funds"][0]["investment_plan_profile"]["enabled"] is True
        assert current["daily_t_items"][0]["level"] == "PLAN"
        assert database_rows(wb) == before
        demo = client.get("/api/v1/state?mode=demo").json()
        assert all(not f["investment_plan_profile"]["enabled"] for f in demo["funds"])
    # A read-only SQLite handle can still project this profile and current holdings.
    with sqlite3.connect(f"file:{wb.store.path}?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        assert wb.snapshot(c)["funds"][0]["investment_plan_profile"]["holding_cap"] == "4000.00"


def test_saving_plan_cap_is_a_reference_limit_and_does_not_force_reclassification(wb):
    seed_plan(wb)
    before = wb.snapshot()
    saved = wb.save_investment_plan(wb.fid, payload(cap="100"))
    assert D(saved["holding_cap"]) < before["funds"][0]["market_value"]
    assert wb.snapshot()["funds"][0]["position_buckets"] == before["funds"][0]["position_buckets"]
