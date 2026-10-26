from decimal import Decimal as D
import pytest

from app.domain.finance import Problem
from app.persistence.store import Store
from app.services.workbench import Workbench
from conftest import opening


def test_profile_is_versioned_per_fund_persistent_and_does_not_change_ledger(wb):
    opening(wb)
    before = wb.snapshot()
    assert before["funds"][0]["daily_t"]["profile"] == {"level": "OBSERVE", "_version": 0}
    one = wb.save_t_profile(wb.fid, {"level": "BALANCED", "expected_version": 0})
    assert one["_version"] == 1
    plan = wb.recommendations()
    assert plan["daily_t_items"][0]["profile_version"] == 1
    with pytest.raises(Problem, match="修改"):
        wb.save_t_profile(wb.fid, {"level": "ACTIVE", "expected_version": 0})
    after = wb.snapshot()
    assert after["revision"] == before["revision"] and after["lots"] == before["lots"]
    assert after["portfolio"] == before["portfolio"] and after["orders"] == before["orders"]
    fresh = Workbench(wb.store, wb.root)
    fresh.now = wb.now
    assert fresh.snapshot()["funds"][0]["daily_t"]["profile"]["level"] == "BALANCED"
    wb.save_t_profile(wb.fid, {"level": "OBSERVE", "expected_version": 1})
    assert wb.recommendations()["id"] != plan["id"]
    second = wb.save_fund({"fund_code": "000002", "name": "隔离合成测试"})
    assert next(f for f in wb.snapshot()["funds"] if f["id"] == second["id"])["daily_t"]["profile"]["level"] == "OBSERVE"
    with wb.store.connect() as c:
        assert len(Store.docs(c, "t_profile_history")) == 2
        assert Store.docs(c, "strategy") == []


@pytest.mark.parametrize("payload", [
    {"level": "OTHER", "expected_version": 0}, {"level": "ACTIVE"},
    {"level": "ACTIVE", "expected_version": True},
    {"level": ["ACTIVE"], "expected_version": 0},
    {"level": "ACTIVE", "expected_version": 0, "amount": "900"},
])
def test_profile_rejects_arbitrary_numbers_and_invalid_preferences(wb, payload):
    with pytest.raises(Problem):
        wb.save_t_profile(wb.fid, payload)
    with wb.store.connect() as c:
        assert not Store.docs(c, "t_profile_history")


def test_real_preopen_uses_today_state_not_latest_nav_growth(wb):
    with wb.store.transaction() as c:
        f = Store.doc(c, "fund", wb.fid)
        Store.put(c, "fund", wb.fid, dict(f, fund_code="012183", share_class="C"), f["_version"])
    wb.observe({"fund_id": wb.fid, "value_kind": "OFFICIAL_NAV", "value": "1", "effective_date": "2026-10-19"})
    wb.observe({"fund_id": wb.fid, "value_kind": "OFFICIAL_NAV", "value": "1.03", "effective_date": "2026-10-20"})
    wb.now = lambda: "2026-10-20T17:10:00Z"
    f = wb.snapshot()["funds"][0]
    assert f["change"] == D(".03")
    assert f["daily_t"]["market"]["date"] == "2026-10-21"
    assert f["daily_t"]["today"]["label"] == "没开盘" and f["daily_t"]["today"]["change"] is None
    assert wb.recommendations()["daily_t_items"][0]["label"] == "没开盘"


def test_demo_current_quote_and_default_observation_do_not_create_profile(tmp_path):
    from app.providers.demo import seed
    w = Workbench(Store(tmp_path / "demo/workbench.sqlite3", "demo"), tmp_path)
    seed(w)
    snapshot = w.snapshot()
    equity = next(f for f in snapshot["funds"] if f["id"] == "demo-equity")
    assert equity["daily_t"]["today"]["status"] == "READY"
    assert equity["daily_t"]["today"]["quote"]["source"].startswith("Demo")
    with w.store.connect() as c:
        assert not Store.docs(c, "t_profile")


def test_new_daily_method_does_not_reuse_previous_version_plan(wb, monkeypatch):
    import app.domain.daily_t as daily
    current = daily.METHOD_VERSION
    monkeypatch.setattr(daily, 'METHOD_VERSION', 'synthetic-prior-daily-method')
    prior = wb.recommendations()
    monkeypatch.setattr(daily, 'METHOD_VERSION', current)
    fresh = wb.recommendations()
    assert fresh['id'] != prior['id']
    assert fresh['daily_t_items'][0]['method_version'] == current
    assert fresh['ledger_version'] == prior['ledger_version']
