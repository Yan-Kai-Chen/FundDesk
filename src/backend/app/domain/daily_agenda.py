"""A compact, read-only agenda over the workbench's existing T references.

Call with references calculated from *the same* snapshot. This module never
chooses a trading amount, persists a plan, fetches prices or posts an order.
Its extra checks only prevent an old/paused reference becoming an action button.
"""
from datetime import time, timedelta
from decimal import Decimal, InvalidOperation

from app.domain.daily_t import fixed_gold_reference
from app.domain.finance import Problem, SHANGHAI, purchase_preview, stamp
from app.domain.quote_phase import source_lunch


METHOD_VERSION = "daily-agenda-v1"
INPUT_STATUSES = {"WAIT_PAYMENT", "WAIT_RULES"}


def _positive(value):
    try:
        number = Decimal(str(value))
        return number.is_finite() and number > 0
    except (InvalidOperation, ValueError, TypeError):
        return False


def _reference_matches(fund, item, now):
    context = fund.get("daily_t") or {}
    profile = (fund.get("investment_plan_profile") or {}) if item.get("level") == "PLAN" else context.get("profile") or {}
    planned = (fund.get("investment_plan_profile") or {}).get("enabled") is True
    if planned != (item.get("level") == "PLAN") or item.get("profile_version") != profile.get("_version"):
        return False
    today = context.get("today") or {}
    if (item.get("observation_id") != (today.get("quote") or {}).get("id") or
            item.get("baseline_id") != (today.get("baseline") or {}).get("id")):
        return False
    if planned and (item.get("plan") or {}).get("template", "DAILY_LADDER_V1") != profile.get("template", "DAILY_LADDER_V1"):
        return False
    try:
        if item.get("expires_at") and stamp(item["expires_at"]) <= stamp(now):
            return False
        if item.get("generated_at") and stamp(item["generated_at"]) > stamp(now):
            return False
    except (Problem, ValueError, TypeError):
        return False
    return True


def _operation_gate(fund, item, now):
    """Only eligibility gates; all amount/fee/profit decisions stay upstream."""
    context = fund.get("daily_t") or {}
    market, today = context.get("market") or {}, context.get("today") or {}
    try:
        local = stamp(now).astimezone(SHANGHAI)
    except (Problem, ValueError, TypeError):
        return "TIME_UNKNOWN", "时间待核对", "无法核对北京时间，暂不生成操作入口"
    if market.get("date") != local.date().isoformat():
        return "STALE_REFERENCE", "参考已更新", "当前参考不是今天的数据，请刷新后查看"
    if not _reference_matches(fund, item, now):
        return "STALE_REFERENCE", "参考已更新", "档位、计划或报价依据已变化，请刷新后查看"
    if local.hour >= 15 or market.get("before_cutoff") is False or market.get("status") == "CLOSED":
        return "CUTOFF", "今日申请已截止", "北京时间 15:00 后仅查看情景，不再生成今日操作入口"
    if market.get("status") == "BREAK" or (market.get("scope") and source_lunch(now, market["scope"])):
        return "BREAK", "午间休市", "保留上午涨跌，下午恢复后重新计算操作参考"
    if market.get("partial_holiday"):
        return "PARTIAL_HOLIDAY", "部分市场休市", "跨市场基金暂停金额参考，等待相关市场恢复"
    if market.get("status") != "OPEN":
        return market.get("status", "UNKNOWN"), market.get("label", "市场未开放"), market.get("detail", "等待参考市场开放")
    if today.get("status") != "READY" or not today.get("quote"):
        return today.get("status", "MISSING"), today.get("label", "等待报价"), "等待有效的当日报价后再计算"
    if item.get("preview_only") is True or item.get("can_apply") is False or (item.get("level") == "PLAN" and item.get("can_apply") is not True):
        if item.get("status") == "REFERENCE" and item.get("action") in {"BUY", "SELL"}:
            return "PREVIEW_ONLY", "参考仅供查看", item.get("action_hint") or "当前参考不能作为今日操作，请刷新后重新核对"
        return item.get("status", "WAIT"), item.get("label", "仅供查看"), item.get("action_hint") or item.get("reason", "当前参考仅供查看")
    return None


def _expires_at(fund, item, now):
    """Bound an already-approved reference by existing quote/session limits."""
    at = stamp(now)
    local = at.astimezone(SHANGHAI)
    context = fund["daily_t"]
    quote, market = context["today"]["quote"], context["market"]
    deadlines = [local.replace(hour=15, minute=0, second=0, microsecond=0)]
    # source_lunch uses the CN stock source's 11:30 pause even for CN_HK.
    if market.get("scope") in {"CN", "CN_HK"} and local.time() < time(11, 30):
        deadlines.append(local.replace(hour=11, minute=30, second=0, microsecond=0))
    if item.get("expires_at"):
        deadlines.append(stamp(item["expires_at"]))
    if quote.get("value_kind") != "OFFICIAL_NAV" and not fixed_gold_reference(quote, market["date"]):
        quoted_at = stamp(quote.get("as_of") or quote.get("observed_at"))
        if quoted_at > at or quoted_at.astimezone(SHANGHAI).date() != local.date():
            return at.isoformat()
        # Same live eligibility window as daily_t.today_quote, without
        # reselecting prices or changing the approved reference amount.
        deadlines.append(quoted_at + timedelta(seconds=900))
    return min(deadlines).isoformat()


def _fund_item(fund, reference, now):
    row = {"id": "fund:" + fund["id"], "fund_id": fund["id"], "fund_code": fund.get("fund_code"),
           "fund_name": (fund.get("sidebar") or {}).get("display_name") or fund.get("name", "基金"),
           "status": "WAIT", "label": "等待计算", "reason": "等待当前基金的做 T 参考",
           "action": "WAIT", "actionable": False, "amount": None, "units": None,
           "fee": None, "net": None, "expected_realized_pnl": None, "prefill": None,
           "basis": None, "expires_at": None}
    if not reference:
        return row
    item = reference
    row.update(status=item.get("status", "WAIT"), label=item.get("label", "等待条件"),
               reason=item.get("reason") or item.get("action_hint") or "等待当前计划条件", action=item.get("action", "WAIT"))
    gate = _operation_gate(fund, item, now)
    if gate:
        row.update(status=gate[0], label=gate[1], reason=gate[2])
        return row
    if item.get("status") != "REFERENCE" or item.get("action") not in {"BUY", "SELL"}:
        return row
    try:
        expires_at = _expires_at(fund, item, now)
        if stamp(expires_at) <= stamp(now):
            return dict(row, status="STALE_REFERENCE", label="参考已过期", reason="等待有效当日报价后再计算")
    except (Problem, KeyError, ValueError, TypeError):
        return dict(row, status="STALE_REFERENCE", label="报价时间待核对", reason="等待有效当日报价后再计算")
    requested = item.get("amount") if item["action"] == "BUY" else item.get("units")
    if not _positive(requested):
        return dict(row, status="REFERENCE_INCOMPLETE", label="参考资料待补", reason="参考金额或份额尚不完整")
    impact = item.get("sell_impact") or {}
    if item["action"] == "SELL" and (impact.get("status") != "READY" or impact.get("expected_realized_pnl") is None):
        return dict(row, status="PNL_UNKNOWN", label="卖出收益待核对", reason="预计净实现收益未知，暂不生成卖出操作入口")
    quote = (fund.get("daily_t") or {}).get("today", {}).get("quote") or {}
    fee = item.get("fee")
    if item["action"] == "BUY" and fee is None:
        # Reuse the existing purchase calculator for fee display only. The
        # requested amount stays byte-for-byte the approved reference amount.
        try:
            fee = purchase_preview(item["amount"], quote["value"], fund["rules"])["fee"]
        except (Problem, KeyError, ValueError, TypeError):
            pass
    row.update(actionable=True, amount=item.get("amount"), units=item.get("units"), fee=fee,
               net=item.get("net"), expected_realized_pnl=impact.get("expected_realized_pnl"),
               expires_at=expires_at,
               prefill={"fund_id": fund["id"], "side": item["action"], "bucket": "TACTICAL", "requested": str(requested)},
               basis={key: quote.get(key) for key in ("id", "value_kind", "effective_date", "as_of", "observed_at", "source")})
    return row


def _order_item(order, fund):
    automation = order.get("automation") or {}
    status = automation.get("status", "MANUAL")
    needs_input = status in INPUT_STATUSES or status == "MANUAL"
    return {"id": "order:" + order["id"], "order_id": order["id"], "fund_id": order["fund_id"],
            "fund_name": (fund.get("sidebar") or {}).get("display_name") or fund.get("name", "基金"),
            "side": order.get("side"), "requested": order.get("requested"), "status": status,
            "label": automation.get("label") or ("待手工确认" if needs_input else "等待处理"),
            "reason": automation.get("detail") or ("此记录未开启自动入仓，可查看或补充实际结果" if needs_input else "已登记，无需重复申请"),
            "confirm_date": automation.get("confirm_date"), "needs_input": needs_input}


def daily_agenda(snapshot, references):
    """Return a pure projection. ``references`` comes from daily_t_references.

    Monetary values retain their source Decimal/string types, with no float
    arithmetic. All buttons prepare existing recording forms; none submit.
    """
    by_reference = {item["fund_id"]: item for item in references or [] if item and item.get("fund_id")}
    funds = {fund["id"]: fund for fund in snapshot.get("funds", [])}
    rows = [_fund_item(fund, by_reference.get(fund["id"]), snapshot["now"]) for fund in funds.values()]
    actions = [row for row in rows if row["actionable"]]
    needs_input, waiting = [], []
    for order in snapshot.get("orders", []):
        if order.get("closed") or order.get("close_reason"):
            continue
        row = _order_item(order, funds.get(order["fund_id"], {}))
        if row["needs_input"]:
            needs_input.append(row)
        else:
            waiting.append(row)
    summary = {"actionable_count": len(actions), "needs_input_count": len(needs_input),
               "waiting_count": len(waiting), "holding_count": len(rows) - len(actions),
               "label": (f"{len(needs_input)} 笔交易需补充" if needs_input else
                         f"{len(actions)} 只基金有操作参考" if actions else "当前无需新增操作")}
    snapshot_basis = []
    for fund in funds.values():
        context = fund.get("daily_t") or {}
        today = context.get("today") or {}
        snapshot_basis.append({"fund_id": fund["id"], "units": fund.get("units"),
                               "rule_version": (fund.get("rules") or {}).get("_version"),
                               "t_profile_version": (context.get("profile") or {}).get("_version"),
                               "investment_plan_version": (fund.get("investment_plan_profile") or {}).get("_version"),
                               "quote_id": (today.get("quote") or {}).get("id"),
                               "baseline_id": (today.get("baseline") or {}).get("id"),
                               "market_date": (context.get("market") or {}).get("date")})
    return {"method_version": METHOD_VERSION, "date": snapshot.get("business_date"), "generated_at": snapshot["now"],
            "timezone": "Asia/Shanghai", "revision": snapshot.get("revision"), "summary": summary,
            "snapshot_basis": snapshot_basis,
            "actions": actions, "needs_input": needs_input, "waiting": waiting, "funds": rows}
