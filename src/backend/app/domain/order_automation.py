"""Local T+1 bookkeeping plans, distinct from a channel confirmation receipt."""
from datetime import timedelta, time, timezone

from app.domain.finance import SHANGHAI, Problem, day, require, stamp, trade_date
from app.domain.market_calendar import CALENDAR_SOURCES, open_day
from app.providers.public_nav import SUPPORTED

AUTO_BASIS = "AUTO_OFFICIAL_NAV"
POLICY_VERSION = "local-t1-2026-v1"
POLICY_SOURCES = [
    "https://edu.gffunds.com.cn/tjjy/gdjcjj/gdjcjj1/202505/t20250521_414424.shtml",
    "https://www.gffunds.com.cn/jjgg/flwj/202503/P020250311328916809957.pdf",
    "https://www.gffunds.com.cn/jjgg/flwj/202305/P020230509351705480941.pdf",
    "https://tkfunds.com.cn/upload/pdf/1764837480177-tkggtzzxgyxtzzsxfqszqtzjjgxzmsms%282025nd2cgx%29.pdf",
    CALENDAR_SOURCES[0],
]


def reference_open(on, *, hong_kong=False):
    require(on.year == 2026, "AUTO_CALENDAR_MISSING", "自动入账日历尚未覆盖该年份")
    return open_day(on, "CN") and (not hong_kong or open_day(on, "HK"))


def next_open(on, count=1):
    for _ in range(count):
        for _ in range(370):
            on += timedelta(days=1)
            if reference_open(on):
                break
        else:
            raise Problem("AUTO_CALENDAR_MISSING", "自动入账日历缺少后续工作日")
    return on.isoformat()


def schedule(order, fund, rule):
    require(fund.get("fund_code") in SUPPORTED and fund.get("share_class") == "C",
            "AUTO_PRODUCT_UNSUPPORTED", "该产品的自动入账规则待接入")
    require(rule.get("verified") is True, "RULES_UNVERIFIED", "等待已核实的产品费用规则")
    require(rule.get("subscriptions_open" if order["side"] == "BUY" else "redemptions_open") is not False,
            "AUTO_PRODUCT_PAUSED", "产品已标记暂停办理，自动入账暂停")
    local = stamp(order["submitted_at"]).astimezone(SHANGHAI)
    if order.get("time_precision") == "BEFORE_CUTOFF":
        require(rule.get("cutoff", "15:00:00") == "15:00:00", "AUTO_TIME_WINDOW_UNKNOWN",
                "该产品截止时间不同，需要补充实际下单时间")
        local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    require(rule.get("effective_from", "0001-01-01") <= local.date().isoformat(),
            "RULES_UNVERIFIED", "现有费用规则未覆盖该笔申请日期")
    if rule.get("calendar_verified") and rule.get("open_dates"):
        trade = trade_date(order["submitted_at"], rule)
        basis = "VERIFIED_FUND_OPEN_DATES"
    else:
        cutoff = time.fromisoformat(rule.get("cutoff", "15:00:00"))
        on = local.date()
        if local.time() > cutoff or (local.time() == cutoff and not rule.get("cutoff_inclusive", False)):
            on += timedelta(days=1)
        # A local normal-dealing assumption, never promoted to a verified fund calendar.
        # HK-exposed funds conservatively skip published full Hong Kong holidays.
        for _ in range(370):
            if reference_open(on, hong_kong=fund["fund_code"] in {"012183", "017954", "006810"}):
                break
            on += timedelta(days=1)
        else:
            raise Problem("AUTO_CALENDAR_MISSING", "缺少下一参考开放日")
        trade, basis = on.isoformat(), "REFERENCE_CN_2026_NORMAL_DEALING"
    confirm = next_open(day(trade))
    return {"trade_date": trade, "confirm_date": confirm,
            "available_from": next_open(day(trade), 2), "calendar_basis": basis,
            "policy_version": POLICY_VERSION,
            "assumption": "普通申赎按T+1自动记账；可赎回日期保守按T+2。临时暂停、限额或渠道差异可校正。"}


def submission_time(value, precision=None):
    parsed = stamp(value)
    if precision == "BEFORE_CUTOFF":
        # Midnight is an ordering representation only; the UI must show the known window.
        parsed = parsed.astimezone(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed.astimezone(timezone.utc).isoformat()
