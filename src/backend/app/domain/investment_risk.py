"""Fixed, read-only multi-session discipline for the V2 investment plan.

This is a position-risk convention, not a fitted return forecast. Formal NAVs
advance a finite-state guard; a current estimate can only make it stricter.
"""
from decimal import Decimal, ROUND_CEILING
import hashlib
import json

from app.domain.finance import ZERO, Problem, day, dec, money, stamp, SHANGHAI
from app.domain.research_calendar import reference_sessions

TEMPLATE_V2 = "DRAWDOWN_GUARD_V2"
METHOD_VERSION_V2 = "investment-plan-2026-v2"
LABELS = {"NORMAL": "正常", "DEFENSIVE": "防御", "PAUSED": "暂停加仓"}
RANK = {"NORMAL": 0, "DEFENSIVE": 1, "PAUSED": 2}
RECOVERY = ["暂停→防御：连续2个正式上涨日，5日复利高于−3h，20日回撤高于−4h",
            "防御→正常：连续2个正式上涨日，5日复利高于−h，20日回撤高于−2h，净值不低于20日均线",
            "每个正式收盘最多恢复一档；盘中估值只能加严，不能解除防御"]


def _threshold(values):
    # Select the last sixty returns before sorting, not the largest sixty.
    moves = sorted(abs(b / a - 1) for a, b in list(zip(values, values[1:]))[-60:])
    if len(moves) < 20:
        return None
    rank = (len(moves) * 60 + 99) // 100
    return (max(Decimal(".01"), moves[rank - 1]) / Decimal(".001")).to_integral_value(rounding=ROUND_CEILING) * Decimal(".001")


def _metrics(values):
    changes = [b / a - 1 for a, b in zip(values, values[1:])]
    negative = positive = 0
    for value in reversed(changes):
        if value < ZERO:
            negative += 1
        else:
            break
    for value in reversed(changes):
        if value > ZERO:
            positive += 1
        else:
            break
    return {"streak": negative, "positive_streak": positive,
            "return_5": values[-1] / values[-6] - 1 if len(values) >= 6 else None,
            "drawdown_20": values[-1] / max(values[-20:]) - 1 if len(values) >= 20 else None,
            "ma20": sum(values[-20:], ZERO) / 20 if len(values) >= 20 else None,
            "ma60": sum(values[-60:], ZERO) / 60 if len(values) >= 60 else None}


def _entered(metrics, h, nav, *, trend=True):
    reasons, pause_reasons, state = [], [], "NORMAL"
    if metrics["streak"] >= 2:
        reasons.append("连续下跌至少2个参考日")
    if metrics["return_5"] <= -2 * h:
        reasons.append("5日复利达到−2h防御线")
    if metrics["drawdown_20"] <= -3 * h:
        reasons.append("20日高点回撤达到−3h防御线")
    if trend and metrics["ma60"] is not None and nav < metrics["ma20"] and metrics["ma20"] < metrics["ma60"]:
        reasons.append("净值低于20日均线，且20日均线低于60日均线")
    if reasons:
        state = "DEFENSIVE"
    if metrics["streak"] >= 4:
        state = "PAUSED"; pause_reasons.append("连续下跌至少4个参考日")
    if metrics["return_5"] <= -4 * h:
        state = "PAUSED"; pause_reasons.append("5日复利达到−4h暂停线")
    if metrics["drawdown_20"] <= -5 * h:
        state = "PAUSED"; pause_reasons.append("20日高点回撤达到−5h暂停线")
    return state, pause_reasons + reasons


def _advance(previous, metrics, h, nav):
    entered, reasons = _entered(metrics, h, nav)
    if RANK[entered] > RANK[previous]:
        return entered, reasons
    if previous == "PAUSED":
        if metrics["positive_streak"] >= 2 and metrics["return_5"] > -3 * h and metrics["drawdown_20"] > -4 * h:
            return "DEFENSIVE", ["两个正式上涨日满足恢复条件，先恢复为防御"]
        return previous, reasons or ["尚未满足暂停后的正式收盘恢复条件"]
    if previous == "DEFENSIVE":
        if metrics["positive_streak"] >= 2 and metrics["return_5"] > -h and metrics["drawdown_20"] > -2 * h and nav >= metrics["ma20"]:
            return "NORMAL", ["正式收盘满足正常档恢复条件"]
        return previous, reasons or ["尚未满足防御后的正式收盘恢复条件"]
    return entered, reasons or ["未触发多日防御条件"]


def _buy_committed(order):
    confirmed = dec(order.get("confirmed_gross", ZERO))
    return confirmed if order.get("closed") else confirmed + max(ZERO, dec(order["requested"]) - confirmed)


def investment_risk(fund, orders, now, cap, pending_buy, holding_value):
    """Return bounded, reconstructible formal-history and capital inputs."""
    market = fund["daily_t"]["market"]
    today = market["date"]
    groups = (fund.get("position_buckets") or {}).get("groups") or {}
    costs = [groups.get(k, {}).get("cost") for k in ("BASE", "TACTICAL")]
    cost = sum((dec(v) for v in costs), ZERO) if all(v is not None for v in costs) else None
    used = None if holding_value is None or cost is None else max(holding_value, cost) + pending_buy
    result = {"ready": False, "state": "PAUSED", "formal_state": "PAUSED", "label": LABELS["PAUSED"],
              "status": "RISK_HISTORY_MISSING", "reasons": [], "h": None,
              "streak": 0, "positive_streak": 0, "return_5": None, "drawdown_20": None,
              "ma20": None, "ma60": None, "provisional": False,
              "rolling_buy_used": ZERO, "rolling_buy_limit": money(cap * Decimal(".075")),
              "rolling_buy_remaining": ZERO, "cooldown_until": None, "cooldown_blocked": False,
              "cost_basis": cost, "capital_used": used,
              "capital_headroom": None if used is None else max(ZERO, cap - used),
              "input_ids": [], "input_basis": {"date": None, "kind": "OFFICIAL_HISTORY", "quote_id": None,
                 "history_end_date": None, "history_count": 0, "history_digest": None, "calendar_scope": "CN_FORMAL_NAV"},
              "recovery_conditions": RECOVERY}

    def wait(status, reason):
        result.update(ready=False, state="PAUSED", label=LABELS["PAUSED"], status=status, reasons=[reason])
        return result

    try:
        visible = [r for r in fund.get("history", []) if r.get("value_kind") == "OFFICIAL_NAV" and
                   r["effective_date"] <= today and stamp(r["observed_at"]) <= stamp(now) and
                   (not r.get("as_of") or stamp(r["as_of"]) <= stamp(now)) and
                   (not r.get("published_at") or stamp(r["published_at"]) <= stamp(now))]
        by_date = {r["effective_date"]: r for r in sorted(visible, key=lambda r: (r["effective_date"], r["observed_at"], r["id"]))}
        rows = list(by_date.values())[-181:]
        values = [dec(r["value"], positive=True) for r in rows]
    except (Problem, KeyError, TypeError):
        return wait("RISK_HISTORY_MISSING", "正式历史的时间或净值资料不完整")
    basis = [{k: r.get(k) for k in ("id", "effective_date", "value", "value_kind", "quality", "observed_at", "as_of", "published_at")} for r in rows]
    result["input_ids"] = [r["id"] for r in rows]
    result["input_basis"].update(date=rows[-1]["effective_date"] if rows else None,
        history_end_date=rows[-1]["effective_date"] if rows else None, history_count=len(rows),
        history_digest=hashlib.sha256(json.dumps(basis, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest())
    if (fund["daily_t"].get("presets", {}).get("blocked_by") == "CORPORATE_ACTION_REVIEW" or
            (fund.get("nav_sync") or {}).get("corporate_actions") or
            (fund.get("nav_sync") or {}).get("corporate_actions_complete") is False):
        return wait("RISK_CORPORATE_REVIEW", "分红、拆分或复权口径待核对，暂停新增金额参考")
    if len(rows) < 21:
        return wait("RISK_HISTORY_MISSING", "需至少21个连续正式净值端点，用于20项收益和多日风险初始化")
    # Domestic funds publish NAV on mainland reference dates even when their
    # underlying CN/HK markets have different holidays. Do not invent HK-only
    # fund NAVs or treat the mainland National Day closure as missing history.
    sessions = reference_sessions()
    if today < sessions[0] or today > sessions[-1] or rows[0]["effective_date"] < sessions[0]:
        return wait("RISK_CALENDAR_MISSING", "正式净值参考日历未覆盖当前历史或日期")
    expected = [d for d in sessions if rows[0]["effective_date"] <= d <= rows[-1]["effective_date"]]
    if [r["effective_date"] for r in rows] != expected:
        return wait("RISK_HISTORY_MISSING", "正式净值历史有参考日缺口，不能把多日变动当作单日收益")
    expected_last = max((d for d in sessions if d < today), default=None)
    if rows[-1]["effective_date"] not in {today, expected_last}:
        return wait("RISK_HISTORY_MISSING", "最近正式净值未覆盖上一参考交易日")
    prior_values = [v for r, v in zip(rows, values) if r["effective_date"] < today]
    h = _threshold(prior_values)
    if h is None:
        return wait("RISK_HISTORY_MISSING", "严格早于今日的正式收益样本不足20项")
    # The first twenty-return window has no earlier state to recover from.
    # Initialize from its observable conditions, then replay one close at a time.
    # This avoids labelling an otherwise flat market as paused forever.
    initial_h = _threshold(values[:21])
    state, reasons = _entered(_metrics(values[:21]), initial_h, values[20])
    reasons = reasons or ["首个20项正式收益窗口未触发多日防御条件"]
    for end in range(21, len(values)):
        historical_h = _threshold(values[:end])
        state, reasons = _advance(state, _metrics(values[:end + 1]), historical_h, values[end])
    metrics = _metrics(values)
    result.update(ready=True, status="READY", h=h, formal_state=state)
    quote = fund["daily_t"]["today"].get("quote")
    if fund["daily_t"]["today"].get("status") == "READY" and quote and quote.get("effective_date") == today:
        try:
            quote_value = dec(quote["value"], positive=True)
            current_values = values[:-1] + [quote_value] if rows[-1]["effective_date"] == today else values + [quote_value]
            current_metrics = _metrics(current_values)
            if quote.get("value_kind") != "OFFICIAL_NAV":
                # MA trend confirmation belongs to disclosed closes. The live
                # overlay only tests provisional streak/5-day/drawdown, so one
                # small dip after flat closes does not invent a trend regime.
                entered, current_reasons = _entered(current_metrics, h, quote_value, trend=False)
                if RANK[entered] > RANK[state]:
                    state, reasons = entered, current_reasons
                current_metrics.update(ma20=metrics["ma20"], ma60=metrics["ma60"])
                metrics = current_metrics
                result.update(provisional=True)
                result["input_basis"].update(kind="HISTORY_AND_CURRENT_QUOTE", quote_id=quote["id"])
        except (Problem, KeyError):
            return wait("RISK_HISTORY_MISSING", "当日报价无法与正式历史衔接")
    result.update(metrics, state=state, label=LABELS[state], reasons=reasons)
    limit = money(cap * (Decimal(".15") if state == "NORMAL" else Decimal(".075")))
    recent_dates = [d for d in sessions if d <= today][-5:]
    own_buys = [o for o in orders if o["fund_id"] == fund["id"] and o.get("bucket") == "TACTICAL" and o["side"] == "BUY"]
    dated = [(stamp(o["submitted_at"]).astimezone(SHANGHAI).date().isoformat(), _buy_committed(o)) for o in own_buys]
    rolling = sum((amount for submitted, amount in dated if recent_dates[0] <= submitted <= today), ZERO)
    active_dates = [submitted for submitted, amount in dated if amount > ZERO and submitted <= today]
    cooldown_until, blocked = None, False
    if active_dates:
        last = max(active_dates)
        if last < today:
            interval = 1 if state == "NORMAL" else 2
            following = [d for d in sessions if d > last]
            cooldown_until = following[interval] if len(following) > interval else None
            blocked = cooldown_until is None or today < cooldown_until
        else:
            cooldown_until = today  # Same-day tier increases may add only the difference.
    result.update(rolling_buy_used=money(rolling), rolling_buy_limit=limit,
                  rolling_buy_remaining=max(ZERO, limit - rolling),
                  cooldown_until=cooldown_until, cooldown_blocked=blocked)
    if cost is None:
        return wait("RISK_COST_MISSING", "两仓剩余经济成本未知，不能核对新增本金风险预算")
    return result
