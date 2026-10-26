from decimal import Decimal as D
import uuid

import pytest
from fastapi.testclient import TestClient

from app.domain.finance import Problem
from app.domain.order_automation import schedule
from app.main import create_app
from app.persistence.store import Store
from app.services.workbench import Workbench


@pytest.fixture
def auto(tmp_path):
    w = Workbench(Store(tmp_path / "data/real/workbench.sqlite3", "real"), tmp_path)
    w.now = lambda: "2026-10-22T09:00:00+00:00"
    w.fid = w.save_fund({"fund_code": "008987", "share_class": "C", "name": "合成黄金自动入账测试"})["id"]
    w.save_rules(w.fid, {"verified": True, "source": "synthetic test only", "effective_from": "2020-01-01",
        "lot_method": "FIFO", "purchase_rate": "0", "unit_step": "0.01", "cutoff": "15:00:00",
        "redemption_tiers": [{"min_days": 0, "max_days": 7, "rate": "0.015"}, {"min_days": 7, "max_days": None, "rate": "0"}]})
    return w


def nav(w, date="2026-10-21", value="1.8516", kind="OFFICIAL_NAV", quality="DISTRIBUTOR_DISCLOSURE"):
    return w.observe({"fund_id": w.fid, "value_kind": kind, "value": value, "effective_date": date,
                      "quality": quality, "source": "synthetic fixture"})


def buy(w, at="2026-10-21T14:57:00+08:00", **extra):
    return w.order({"fund_id": w.fid, "side": "BUY", "requested": "200", "bucket": "BASE",
                    "paid_immediately": True, "key": str(uuid.uuid4()), "occurred_at": at, **extra})["order_id"]


def edit(w, oid, **extra):
    return w.edit_order(oid, {"key": str(uuid.uuid4()), "expected_revision": w.snapshot()["revision"], **extra})


@pytest.mark.parametrize("at,trade,confirm", [
    ("2026-10-21T14:59:59+08:00", "2026-10-21", "2026-10-22"),
    ("2026-10-21T15:00:00+08:00", "2026-10-22", "2026-10-23"),
    ("2026-10-22T18:00:00+09:00", "2026-10-23", "2026-10-26"),
    ("2026-10-23T15:01:00+08:00", "2026-10-26", "2026-10-27"),
    ("2026-10-10T12:00:00+08:00", "2026-10-12", "2026-10-13"),
    ("2026-09-30T15:01:00+08:00", "2026-10-08", "2026-10-09"),
])
def test_actual_time_cutoff_weekends_and_holidays(auto, at, trade, confirm):
    with auto.store.connect() as c:
        plan = schedule({"submitted_at": at, "side": "BUY"}, auto.fund(c, auto.fid), auto.rules(c, auto.fid))
    assert (plan["trade_date"], plan["confirm_date"]) == (trade, confirm)
    assert plan["calendar_basis"] == "REFERENCE_CN_2026_NORMAL_DEALING"


def test_missing_year_hk_holiday_and_verified_product_calendar(auto):
    with auto.store.connect() as c:
        f, rule = auto.fund(c, auto.fid), auto.rules(c, auto.fid)
    with pytest.raises(Problem, match="年份"):
        schedule({"submitted_at": "2027-01-04T14:00:00+08:00", "side": "BUY"}, f, rule)
    f["fund_code"] = "012183"
    assert schedule({"submitted_at": "2026-10-19T14:00:00+08:00", "side": "BUY"}, f, rule)["trade_date"] == "2026-10-20"
    explicit = dict(rule, calendar_verified=True, open_dates=["2026-10-20", "2026-10-22"])
    p = schedule({"submitted_at": "2026-10-21T14:00:00+08:00", "side": "BUY"}, f, explicit)
    assert p["trade_date"] == "2026-10-22" and p["calendar_basis"] == "VERIFIED_FUND_OPEN_DATES"


def test_hong_kong_bank_pending_purchase_uses_own_dealing_day(auto):
    with auto.store.connect() as c:
        f, rule = auto.fund(c, auto.fid), auto.rules(c, auto.fid)
    f["fund_code"] = "006810"
    p = schedule({"submitted_at": "2026-10-19T14:53:45+08:00", "side": "BUY"}, f, rule)
    assert p["trade_date"] == "2026-10-20"  # HK holiday despite an open mainland market
    p = schedule({"submitted_at": "2026-10-23T14:53:45+08:00", "side": "BUY"}, f, rule)
    assert (p["trade_date"], p["confirm_date"], p["available_from"]) == ("2026-10-23", "2026-10-26", "2026-10-27")


def test_buy_books_once_in_correct_bucket_without_cash_or_channel_receipt(auto):
    oid = buy(auto, bucket="TACTICAL"); observation = nav(auto)
    before = auto.snapshot()
    assert before["portfolio"]["cash"] == 0 and before["orders"][0]["automation"]["status"] == "READY"
    assert auto.auto_post_orders()["processed"] == 1
    s = auto.snapshot(); o = s["orders"][0]
    assert o["id"] == oid and o["confirmed_units"] == D("108.01") and o["closed"]
    assert o["automation"]["label"] == "已自动入仓"
    assert o["confirmations"][0]["nav_observation_id"] == observation["id"]
    assert o["confirmations"][0]["confirmation_basis"] == "AUTO_OFFICIAL_NAV"
    assert s["lots"][0]["basis"] == "AUTO_OFFICIAL_NAV"
    assert s["funds"][0]["position_buckets"]["groups"]["TACTICAL"]["units"] == D("108.01")
    assert s["funds"][0]["position_buckets"]["groups"]["BASE"]["units"] == 0
    assert s["portfolio"]["cash"] == s["portfolio"]["pending_buy"] == 0
    assert s["portfolio"]["pnl"] == D("-0.01")
    assert s["lots"][0]["acquired"] == "2026-10-22" and s["lots"][0]["available_from"] == "2026-10-23"
    for _ in range(3):
        assert auto.auto_post_orders()["processed"] == 0
    assert auto.snapshot()["revision"] == s["revision"]
    assert auto.snapshot()["orders"][0]["confirmations"] == o["confirmations"]


def test_only_exact_disclosed_nav_and_t1_can_post(auto):
    oid = buy(auto, "2026-10-22T14:00:00+08:00")
    nav(auto, "2026-10-22", "2")
    assert auto.auto_post_orders()["processed"] == 0  # same day is not T+1
    auto.now = lambda: "2026-10-23T02:00:00+00:00"
    assert auto.auto_post_orders()["processed"] == 1
    assert auto.snapshot()["orders"][0]["confirmed_units"] == D("100")
    assert auto.snapshot()["orders"][0]["id"] == oid


@pytest.mark.parametrize("kind,quality,date", [
    ("LOCAL_ESTIMATE", "LOCAL_GOLD_REFERENCE", "2026-10-21"),
    ("THIRD_PARTY_ESTIMATE", "PUBLIC_THIRD_PARTY_ESTIMATE", "2026-10-21"),
    ("OFFICIAL_NAV", "MANUAL", "2026-10-21"),
    ("OFFICIAL_NAV", "DISTRIBUTOR_DISCLOSURE", "2026-10-20"),
])
def test_estimates_manual_unverified_and_wrong_day_never_become_confirmation(auto, kind, quality, date):
    buy(auto); nav(auto, date, "2", kind, quality)
    assert auto.auto_post_orders()["processed"] == 0
    assert auto.snapshot()["orders"][0]["automation"]["status"] == "WAIT_NAV"
    assert auto.snapshot()["portfolio"]["pending_buy"] == D("200")


@pytest.mark.parametrize("extra,status", [({"paid_immediately": False}, "WAIT_PAYMENT"), ({"auto_confirm": False}, "MANUAL")])
def test_unpaid_or_opted_out_never_posts(auto, extra, status):
    buy(auto, **extra); nav(auto)
    assert auto.auto_post_orders()["processed"] == 0
    assert auto.snapshot()["orders"][0]["automation"]["status"] == status


def test_manual_canceled_and_partial_orders_are_not_overwritten(auto):
    oid = buy(auto); nav(auto)
    auto.event("ORDER_CANCEL_REQUESTED", {"order_id": oid}, str(uuid.uuid4()), auto.now())
    assert auto.auto_post_orders()["processed"] == 0
    assert auto.snapshot()["orders"][0]["automation"]["status"] == "PAUSED"
    auto.event("ORDER_CANCELED", {"order_id": oid}, str(uuid.uuid4()), auto.now())
    assert auto.auto_post_orders()["processed"] == 0
    other = buy(auto)
    auto.event("ORDER_CONFIRMED", {"order_id": other, "units": "50", "nav": "2", "gross": "100", "fee": "0", "net": "100",
        "nav_date": "2026-10-21", "confirm_date": "2026-10-22", "available_from": "2026-10-23", "is_final": False}, str(uuid.uuid4()), auto.now())
    revision = auto.snapshot()["revision"]
    assert auto.auto_post_orders()["processed"] == 0 and auto.snapshot()["revision"] == revision


def test_actual_correction_wins_and_reversal_does_not_regenerate(auto):
    buy(auto); nav(auto); auto.auto_post_orders(); s = auto.snapshot()
    e = s["orders"][0]["automation"]["correction_event"]
    p = dict(e["payload"], units="100", nav="2")
    auto.correct({"event_id": e["id"], "key": str(uuid.uuid4()), "reason": "synthetic actual result", "occurred_at": auto.now(),
                  "expected_revision": s["revision"], "replacement": {"kind": e["kind"], "payload": p, "occurred_at": e["occurred_at"]}})
    assert auto.auto_post_orders()["processed"] == 0
    s = auto.snapshot(); o = s["orders"][0]
    assert o["confirmed_units"] == D("100") and not o["automation"]["automatic"]
    assert o["confirmations"][0]["confirmation_basis"] == "CHANNEL_RECORD"
    assert len(s["lots"]) == 1 and s["lots"][0]["id"] == e["id"]
    # A user reversal intentionally pauses automatic reconstruction.
    other = buy(auto); auto.auto_post_orders(); s = auto.snapshot()
    e = next(o for o in s["orders"] if o["id"] == other)["automation"]["correction_event"]
    auto.correct({"event_id": e["id"], "key": str(uuid.uuid4()), "reason": "undo automatic result", "occurred_at": auto.now(), "expected_revision": s["revision"]})
    assert auto.auto_post_orders()["processed"] == 0
    assert next(o for o in auto.snapshot()["orders"] if o["id"] == other)["automation"]["status"] == "OVERRIDDEN"


def test_late_registration_edit_uses_actual_beijing_time_and_not_recorded_time(auto):
    auto.now = lambda: "2026-10-23T08:00:00+00:00"
    oid = buy(auto, "2026-10-22T18:00:00+09:00")  # Beijing 17:00, late registration
    nav(auto, "2026-10-22", "2")
    assert auto.auto_post_orders()["processed"] == 0
    original = auto.snapshot()["orders"][0]
    assert original["automation"]["trade_date"] == "2026-10-23"
    edit(auto, oid, submitted_at="2026-10-22T14:30:00+08:00")
    o = auto.snapshot()["orders"][0]
    assert o["id"] == oid and o["confirmed_units"] == D("100")
    assert o["confirmations"][0]["nav_date"] == "2026-10-22"
    assert o["submitted_at"] == "2026-10-22T06:30:00+00:00"
    assert o["recorded_at"] == original["recorded_at"]
    assert len(auto.snapshot()["orders"]) == len(auto.snapshot()["lots"]) == 1


def test_known_before_cutoff_window_does_not_invent_an_exact_time(auto):
    oid = buy(auto, "2026-10-22T17:00:00+08:00")
    original = auto.snapshot()["orders"][0]
    edit(auto, oid, submitted_at="2026-10-22T00:00:00+08:00", time_precision="BEFORE_CUTOFF")
    s = auto.snapshot(); o = s["orders"][0]
    assert o["time_precision"] == "BEFORE_CUTOFF" and o["submission_basis"] == "USER_STATED_BEFORE_1500"
    assert o["submitted_at"] == "2026-10-21T16:00:00+00:00"  # ordering only; no clock in UI
    assert o["recorded_at"] == original["recorded_at"]
    assert o["automation"]["trade_date"] == "2026-10-22" and o["automation"]["confirm_date"] == "2026-10-23"
    payment = next(e for e in s["audit"] if e["kind"] == "ORDER_PAID" and not e["reversed"])
    assert payment["payload"]["time_precision"] == "DAY"
    nav(auto, "2026-10-22", "2")
    assert auto.auto_post_orders()["processed"] == 0
    auto.now = lambda: "2026-10-23T02:00:00+00:00"
    assert auto.auto_post_orders()["processed"] == 1
    assert auto.snapshot()["orders"][0]["confirmations"][0]["submission_precision"] == "BEFORE_CUTOFF"


def test_new_before_cutoff_entry_normalizes_order_and_paid_day_without_double_charging(auto):
    oid = buy(auto, "2026-10-21T14:00:00+08:00", time_precision="BEFORE_CUTOFF")
    nav(auto); auto.auto_post_orders(); o = auto.snapshot()["orders"][0]
    assert o["id"] == oid and o["time_precision"] == "BEFORE_CUTOFF"
    assert o["paid"] == D("200") and o["confirmed_units"] == D("108.01")


def test_edit_automatically_booked_purchase_recalculates_cost_bucket_and_keeps_identity(auto):
    oid = buy(auto); nav(auto); auto.auto_post_orders(); before = auto.snapshot()
    original_lot = before["lots"][0]["id"]
    p = {"key": str(uuid.uuid4()), "expected_revision": before["revision"], "requested": "300", "paid_amount": "300", "bucket": "TACTICAL"}
    auto.edit_order(oid, p)
    assert auto.edit_order(oid, p)["duplicate"]
    with pytest.raises(Problem, match="同一修改"):
        auto.edit_order(oid, dict(p, requested="400"))
    s = auto.snapshot(); o = s["orders"][0]
    assert len(s["lots"]) == len(s["orders"]) == 1 and s["lots"][0]["id"] == original_lot
    assert o["confirmed_units"] == D("162.02") and o["confirmed_gross"] == D("300")
    groups = s["funds"][0]["position_buckets"]["groups"]
    assert groups["BASE"]["units"] == 0 and groups["TACTICAL"]["units"] == D("162.02")
    assert s["portfolio"]["cash"] == s["portfolio"]["pending_buy"] == 0
    assert s["portfolio"]["pnl"] == D("0")
    assert auto.auto_post_orders()["processed"] == 0


def test_edit_to_later_date_reopens_until_correct_nav_and_confirmation_day(auto):
    oid = buy(auto); nav(auto); auto.auto_post_orders()
    edit(auto, oid, submitted_at="2026-10-22T14:00:00+08:00")
    s = auto.snapshot()
    assert not s["lots"] and s["orders"][0]["automation"]["confirm_date"] == "2026-10-23"
    assert s["portfolio"]["pending_buy"] == D("200")
    auto.now = lambda: "2026-10-23T03:00:00+00:00"
    nav(auto, "2026-10-22", "2")
    assert auto.auto_post_orders()["processed"] == 1
    assert auto.snapshot()["orders"][0]["confirmed_units"] == D("100")


def test_invalid_edit_is_atomic_and_stale_metadata_is_rejected(auto):
    oid = buy(auto); nav(auto); auto.auto_post_orders(); before = auto.snapshot()
    with pytest.raises(Problem):
        edit(auto, oid, requested="100", paid_amount="200")
    assert auto.snapshot() == before
    p = {"key": str(uuid.uuid4()), "expected_revision": before["revision"], "expected_order_updated_at": before["orders"][0]["updated_at"], "external_order_id": "first-edit"}
    auto.edit_order(oid, p)
    with pytest.raises(Problem, match="已被修改"):
        auto.edit_order(oid, dict(p, key=str(uuid.uuid4()), external_order_id="another-edit"))


def test_edit_can_survive_automatic_confirmation_while_dialog_is_open(auto):
    oid = buy(auto); nav(auto); before = auto.snapshot()
    auto.auto_post_orders()
    auto.edit_order(oid, {"key": str(uuid.uuid4()), "expected_revision": before["revision"],
        "expected_order_updated_at": before["orders"][0]["updated_at"], "bucket": "TACTICAL"})
    assert auto.snapshot()["orders"][0]["bucket"] == "TACTICAL"


def test_metadata_edit_after_later_sale_preserves_the_purchase_and_sale(auto):
    oid = buy(auto); nav(auto); auto.auto_post_orders()
    auto.now = lambda: "2026-10-23T08:00:00+00:00"
    sale = auto.order({"fund_id": auto.fid, "side": "SELL", "bucket": "BASE", "requested": "10",
        "occurred_at": "2026-10-23T14:00:00+08:00", "key": "later-sale-reserved"})["order_id"]
    before = auto.snapshot()
    edit(auto, oid, external_order_id="corrected-channel-number")
    s = auto.snapshot()
    assert s["lots"] == before["lots"] and s["portfolio"] == before["portfolio"]
    assert next(o for o in s["orders"] if o["id"] == sale)["allocations"] == next(o for o in before["orders"] if o["id"] == sale)["allocations"]
    assert next(o for o in s["orders"] if o["id"] == oid)["external_order_id"] == "corrected-channel-number"
    revision = s["revision"]
    with pytest.raises(Problem, match="后续卖出"):
        edit(auto, oid, requested="1", paid_amount="1")
    assert auto.snapshot()["revision"] == revision


def test_sell_uses_reserved_fifo_fees_t_first_and_does_not_claim_bank_receipt(auto):
    auto.event("OPENING_LOT", {"fund_id": auto.fid, "units": "100", "cost": "100", "acquired": "2026-10-15",
        "available_from": "2026-10-16", "bucket": "BASE"}, "auto-sell-base-opening", "2026-10-16T02:00:00Z")
    auto.event("OPENING_LOT", {"fund_id": auto.fid, "units": "50", "cost": "50", "acquired": "2026-10-16",
        "available_from": "2026-10-19", "bucket": "TACTICAL"}, "auto-sell-t-opening", "2026-10-19T02:00:00Z")
    sale = auto.order({"fund_id": auto.fid, "side": "SELL", "bucket": "TACTICAL", "requested": "60",
        "occurred_at": "2026-10-21T14:00:00+08:00", "key": "auto-sell-t-request"})["order_id"]
    nav(auto, value="2"); assert auto.auto_post_orders()["processed"] == 1
    s = auto.snapshot(); o = s["orders"][0]; g = s["funds"][0]["position_buckets"]
    assert o["id"] == sale and o["confirmed_net"] == D("120")  # buy 10/15 to redeem confirm 10/22 = 7 days
    assert g["sales"][-1]["t_units"] == D("50") and g["sales"][-1]["base_units"] == D("10")
    assert g["groups"]["TACTICAL"]["realized"] == D("60") and g["groups"]["BASE"]["realized"] == 0
    assert o["settled"] == s["portfolio"]["cash"] == 0
    assert s["portfolio"]["redemption_due"] == D("120")
    assert "回款单独记录" in o["automation"]["detail"]


def test_api_edit_and_process_are_csrf_protected_and_get_state_is_read_only(auto):
    oid = buy(auto); nav(auto)
    app = create_app(auto.root, testing=True)
    app.state.workbenches["real"].now = auto.now
    with TestClient(app) as client:
        csrf = client.get("/api/v1/session").json()["csrf_token"]
        before = client.get("/api/v1/state?mode=real").json()
        assert client.get("/api/v1/state?mode=real").json()["revision"] == before["revision"]
        assert client.post("/api/v1/orders/process?mode=real").status_code == 403
        client.headers["x-csrf-token"] = csrf
        assert client.post("/api/v1/orders/process?mode=real").json()["processed"] == 1
        body = {"bucket": "TACTICAL", "expected_revision": auto.snapshot()["revision"], "key": "api-order-edit-key"}
        assert client.post(f"/api/v1/orders/{oid}/edit?mode=real", json=body).status_code == 200
        assert client.get("/api/v1/state?mode=real").json()["orders"][0]["bucket"] == "TACTICAL"
        assert client.post("/api/v1/orders/process?mode=demo").json()["processed"] == 0
