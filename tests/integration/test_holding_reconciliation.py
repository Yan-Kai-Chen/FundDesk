"""Reconciliation is evidence, not an alternative way to change a position."""
from decimal import Decimal

import pytest

from app.domain.finance import Problem
from app.persistence.store import Store, pack
from app.services.reconciliation import HISTORY_LIMIT, KIND, ReconciliationMixin
from app.services.workbench import Workbench
from conftest import opening, order


@pytest.fixture
def reconciler(wb):
    cls = Workbench if issubclass(Workbench, ReconciliationMixin) else type("ReconciliationWorkbench", (ReconciliationMixin, Workbench), {})
    w = cls(wb.store, wb.root)
    w.now, w.fid = wb.now, wb.fid
    opening(w, units="100.12", cost="110")
    w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "value": "1.20", "effective_date": "2026-10-19"})
    return w


def draft(w, **updates):
    return dict({"source": "支付宝合成测试", "snapshot_at": w.now(), "scope": "CURRENT",
                 "units_basis": "CONFIRMED_EXCLUDES_PENDING", "confirmed_units": "100.12"}, **updates)


def save(w, p):
    preview = w.preview_reconciliation(w.fid, p)
    return w.save_reconciliation(w.fid, dict(preview["input"], preview_token=preview["preview_token"], save_snapshot=True))


def financial_facts(w):
    with w.store.connect() as c:
        return pack({"events": Store.events(c), "funds": Store.docs(c, "fund"), "rules": Store.docs(c, "rule"),
                     "observations": [dict(r) for r in c.execute("SELECT * FROM observations")]})


def test_preview_is_read_only_and_only_explicit_snapshot_saves(reconciler):
    w = reconciler
    before = financial_facts(w)
    for _ in range(5):
        p = w.preview_reconciliation(w.fid, draft(w))
        assert p["status"] == "MATCHED" and p["comparisons"][0]["difference"] == 0
    with w.store.connect() as c:
        assert Store.docs(c, KIND) == []
    with pytest.raises(Problem, match="明确保存"):
        w.save_reconciliation(w.fid, dict(p["input"], preview_token=p["preview_token"]))
    result = save(w, draft(w))
    assert result["status"] == "MATCHED" and result["duplicate"] is False
    assert financial_facts(w) == before
    with w.store.connect() as c:
        assert w.reconciliation_summary(c, w.fid)["needs_attention"] is False


def test_difference_is_exact_and_shows_existing_order_action(reconciler):
    w = reconciler
    oid = order(w, requested="200", cash_source="EXTERNAL")
    p = w.preview_reconciliation(w.fid, draft(w, confirmed_units="100.13"))
    assert p["status"] == "DIFFERENCE" and p["difference_count"] == 1
    assert p["comparisons"][0]["difference"] == Decimal("0.01")
    assert p["baseline"]["confirmed_units"] == Decimal("100.12")
    assert p["baseline"]["pending_order_count"] == 1
    assert p["correction_action"]["order_ids"] == [oid]
    result = save(w, draft(w, confirmed_units="100.13"))
    assert result["baseline"]["confirmed_units"] == Decimal("100.12")
    with w.store.connect() as c:
        assert w.reconciliation_summary(c, w.fid)["needs_attention"] is True


@pytest.mark.parametrize("updates", [{"scope": "HISTORICAL"}, {"snapshot_at": "2026-10-19T05:30:00Z"},
                                    {"snapshot_at": "2026-10-20T05:19:59Z"}])
def test_historical_or_old_snapshot_never_compares_current_position(reconciler, updates):
    w = reconciler
    p = w.preview_reconciliation(w.fid, draft(w, confirmed_units="1", **updates))
    assert p["status"] == "NOT_COMPARABLE" and p["comparisons"] == []
    assert save(w, draft(w, confirmed_units="1", **updates))["status"] == "NOT_COMPARABLE"


def test_change_after_capture_is_not_treated_as_current(reconciler):
    w = reconciler
    opening(w, units="1", cost="1", id="new-lot", available="2026-10-20")
    w.now = lambda: "2026-10-20T04:05:00Z"
    p = w.preview_reconciliation(w.fid, draft(w, snapshot_at="2026-10-20T03:59:00Z"))
    assert p["status"] == "NOT_COMPARABLE"
    assert "截图后" in p["unassessed"][0]["reason"]


def test_amounts_only_compare_same_official_day_and_matching_profit_basis(reconciler):
    w = reconciler
    p = draft(w, market_value="120.14", holding_pnl="10.14", nav_date="2026-10-19", valuation_basis="OFFICIAL_NAV", holding_pnl_basis="REMAINING_COST")
    result = w.preview_reconciliation(w.fid, p)
    assert len(result["comparisons"]) == 3 and result["status"] == "MATCHED"
    assert result["comparisons"][1]["local"] == Decimal("120.14")
    assert result["comparisons"][2]["local"] == Decimal("10.14")
    for updates in [{"valuation_basis": "ESTIMATE"}, {"nav_date": "2026-10-18"}, {"valuation_basis": "UNSPECIFIED"}]:
        result = w.preview_reconciliation(w.fid, dict(p, **updates))
        assert len(result["comparisons"]) == 1 and len(result["unassessed"]) == 2
    result = w.preview_reconciliation(w.fid, dict(p, holding_pnl_basis="UNSPECIFIED", reported_fee="1.23"))
    assert [r["field"] for r in result["comparisons"]] == ["confirmed_units", "market_value"]
    assert [r["field"] for r in result["unassessed"]] == ["holding_pnl", "reported_fee"]


def test_saved_basis_freezes_and_later_holdings_change_marks_stale(reconciler):
    w = reconciler
    saved = save(w, draft(w))
    opening(w, units="2", cost="2", id="extra")
    with w.store.connect() as c:
        result = w.reconciliation_summary(c, w.fid)
        assert result["status"] == "STALE" and result["needs_attention"] is False
        assert result["latest"]["frozen_status"] == "MATCHED"
        assert dec_string(result["latest"]["baseline"]["confirmed_units"]) == Decimal("100.12")
        assert result["latest"]["id"] == saved["id"]


def dec_string(value):
    return Decimal(str(value))


def test_units_only_snapshot_survives_nav_update_other_fund_and_cash(reconciler):
    w = reconciler
    p = w.preview_reconciliation(w.fid, draft(w))
    other = w.save_fund({"fund_code": "000002", "name": "另一只合成基金"})
    w.event("OPENING_LOT", {"fund_id": other["id"], "lot_id": "other-lot", "units": "3", "cost": "3", "acquired": "2026-06-01", "available_from": "2026-06-03"}, "other-opening", "2026-10-20T04:00:00Z")
    w.event("CASH_DEPOSIT", {"amount": "10"}, "cash-only-change", "2026-10-20T04:00:00Z")
    w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "value": "1.21", "effective_date": "2026-10-20"})
    saved = w.save_reconciliation(w.fid, dict(p["input"], preview_token=p["preview_token"], save_snapshot=True))
    assert saved["status"] == "MATCHED"
    with w.store.connect() as c:
        assert w.reconciliation_summary(c, w.fid)["status"] == "MATCHED"


def test_price_sensitive_snapshot_expires_on_formal_revision(reconciler):
    w = reconciler
    save(w, draft(w, market_value="120.14", valuation_basis="OFFICIAL_NAV", nav_date="2026-10-19"))
    w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "value": "1.21", "effective_date": "2026-10-20"})
    with w.store.connect() as c:
        assert w.reconciliation_summary(c, w.fid)["status"] == "STALE"


def test_changed_input_or_same_fund_basis_requires_new_preview(reconciler):
    w = reconciler
    p = w.preview_reconciliation(w.fid, draft(w))
    for altered in [dict(p["input"], confirmed_units="2"), p["input"]]:
        if altered == p["input"]:
            opening(w, units="2", cost="2", id="after-preview")
        with pytest.raises(Problem, match="重新核对"):
            w.save_reconciliation(w.fid, dict(altered, preview_token=p["preview_token"], save_snapshot=True))
    with w.store.connect() as c:
        assert Store.docs(c, KIND) == []


def test_saved_history_is_bounded_and_duplicate_does_not_append(reconciler):
    w = reconciler
    before = financial_facts(w)
    results = [save(w, draft(w, confirmed_units=str(i))) for i in range(8)]
    duplicate = save(w, draft(w, confirmed_units="7"))
    assert duplicate["duplicate"] is True
    catalog = w.reconciliation_catalog()
    assert catalog["funds"][0]["latest"]["id"] == results[-1]["id"]
    assert catalog["funds"][0]["history_count"] == HISTORY_LIMIT
    assert financial_facts(w) == before


@pytest.mark.parametrize("change", [{"confirmed_units": 100.12}, {"confirmed_units": None}, {"confirmed_units": "-1"},
                                    {"units_basis": "INCLUDING_PENDING"}, {"snapshot_at": "2026-10-21T00:00:00Z"},
                                    {"market_value": "1.001"}, {"source": ""}])
def test_invalid_snapshot_is_atomic(reconciler, change):
    w = reconciler
    with pytest.raises(Problem):
        w.preview_reconciliation(w.fid, draft(w, **change))
    with w.store.connect() as c:
        assert Store.docs(c, KIND) == []


def test_uninitialized_statement_is_not_misrepresented_as_empty_position(wb):
    cls = Workbench if issubclass(Workbench, ReconciliationMixin) else type("ReconciliationWorkbench", (ReconciliationMixin, Workbench), {})
    w = cls(wb.store, wb.root);w.now, w.fid = wb.now, wb.fid
    with w.store.transaction() as c:
        Store.put(c, "statement", w.fid, {"fund_id": w.fid, "rows": []})
    result = w.preview_reconciliation(w.fid, draft(wb, confirmed_units="100"))
    assert result["status"] == "NOT_COMPARABLE" and "尚未建立" in result["unassessed"][0]["reason"]
