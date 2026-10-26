"""Read-only daily ladder for one fund, independent of account cash or forecasts.

The ladder is an execution convention, not a fitted prediction of excess return.
Physical FIFO lots determine fees; the economic T book limits the suggested sale.
"""
from decimal import Decimal, ROUND_FLOOR

from app.domain.finance import ZERO, Problem, day, dec, fee_rate, money, redemption_preview, stamp, SHANGHAI
from app.domain.quote_phase import source_lunch
from app.domain.sell_impact import sell_impact
from app.domain.sell_comparison import buy_price_comparison
from app.domain.investment_risk import investment_risk, TEMPLATE_V2, METHOD_VERSION_V2

METHOD_VERSION = "investment-plan-2026-v1"
TEMPLATE = "DAILY_LADDER_V1"
TIERS = (("0.01", "0.05"), ("0.02", "0.10"), ("0.03", "0.15"))


def _remaining(order):
    key = "confirmed_gross" if order["side"] == "BUY" else "confirmed_units"
    return max(ZERO, dec(order["requested"]) - dec(order.get(key, ZERO)))


def _committed(order, nav):
    confirmed = dec(order.get("confirmed_gross", ZERO))
    if order.get("closed"):
        return confirmed
    remaining = _remaining(order)
    return confirmed + (remaining if order["side"] == "BUY" else money(remaining * nav))


def investment_plan_reference(fund, orders, now):
    """Return a repeatable scenario from the current quote and submitted orders.

    Nothing is posted, reserved, fetched or cached. A paused-session scenario may
    retain an amount while ``can_apply`` is false; callers must honor that flag.
    """
    profile = fund.get("investment_plan_profile") or {}
    if not profile.get("enabled"):
        return None
    cap = money(dec(profile["holding_cap"], positive=True))
    v2 = profile.get("template") == TEMPLATE_V2
    context = fund["daily_t"]
    market, today = context["market"], context["today"]
    quote, baseline = today.get("quote"), today.get("baseline")
    groups = (fund.get("position_buckets") or {}).get("groups") or {}
    base, tactical = groups.get("BASE", {}), groups.get("TACTICAL", {})
    own_orders = [o for o in orders if o["fund_id"] == fund["id"]]
    pending_buy = sum((_remaining(o) for o in own_orders if o["side"] == "BUY" and not o.get("closed")), ZERO)
    official_nav = fund.get("valuation_nav")
    official_row = fund.get("nav") or {}
    confirmed_units = fund.get("units", (fund.get("position_buckets") or {}).get("total_units"))
    official_value = fund.get("market_value")
    if official_value is not None:
        official_value = dec(official_value)
    if official_value is None and official_nav is not None and confirmed_units is not None:
        official_value = money(dec(confirmed_units) * dec(official_nav, positive=True))
    base_value = base.get("value")
    if base_value is not None:
        base_value = dec(base_value)
    if base_value is None and official_nav is not None and base.get("units") is not None:
        base_value = money(dec(base["units"]) * dec(official_nav, positive=True))
    committed = None if official_value is None else official_value + pending_buy
    tiers = [{"trigger": t, "fraction": f, "amount": money(cap * dec(f))} for t, f in TIERS]
    daily_budget = tiers[-1]["amount"]
    plan = {"holding_cap": cap, "holding_value": official_value, "pending_buy": pending_buy,
            "committed_holding": committed, "headroom": None if committed is None else max(ZERO, cap - committed),
            "base_value": base_value, "base_units": base.get("units"),
            "t_value": official_value - base_value if official_value is not None and base_value is not None else tactical.get("value"),
            "t_available_units": tactical.get("available_units"),
            "valuation_basis": "OFFICIAL_NAV" if official_value is not None else None,
            "valuation_date": official_row.get("effective_date"),
            "tiers": tiers, "target_today": ZERO, "used_buy": ZERO, "used_sell": ZERO,
            "remaining_daily": daily_budget, "template": profile.get("template", TEMPLATE),
            "profile_version": profile.get("_version", 0)}
    if v2:
        risk_value = official_value
        if today.get("status") == "READY" and quote and confirmed_units is not None:
            risk_value = money(dec(confirmed_units) * dec(quote["value"], positive=True))
        risk = investment_risk(fund, own_orders, now, cap, pending_buy, risk_value)
        plan.update(risk=risk, raw_target_today=ZERO, committed_holding=risk["capital_used"],
                    headroom=risk["capital_headroom"])
        h = risk["h"] or Decimal(".01")
        tiers = [{"trigger": h * (i + 1), "fraction": fraction, "amount": money(cap * dec(fraction))}
                 for i, (_, fraction) in enumerate(TIERS)]
        plan["tiers"] = tiers
    item = {"fund_id": fund["id"], "action": "WAIT", "status": "WAIT", "label": "等待今日涨跌",
            "reason": "", "amount": None, "units": None, "fee": None, "net": None,
            "sell_impact": None, "method_version": METHOD_VERSION_V2 if v2 else METHOD_VERSION,
            "profile_version": profile.get("_version", 0), "level": "PLAN",
            "observation_id": (quote or {}).get("id"), "baseline_id": (baseline or {}).get("id"),
            "single_budget": ZERO, "daily_budget": daily_budget, "used_today": ZERO,
            "cash_status": "REFERENCE_ONLY", "plan": plan, "can_apply": False,
            "preview_only": False, "reference_label": "当日涨跌阶梯", "action_hint": "等待有效当日报价"}

    def wait(status, label, reason, hold=False):
        return dict(item, status=status, label=label, reason=reason, action="HOLD" if hold else "WAIT",
                    amount=ZERO if hold else None, can_apply=False, action_hint=reason)

    if plan["template"] not in {TEMPLATE, TEMPLATE_V2}:
        return wait("PLAN_UNSUPPORTED", "计划模板待核对", "当前版本无法计算此计划模板")
    if market["status"] in {"NOT_OPEN", "UNKNOWN"}:
        return wait(market["status"], market["label"], market.get("detail", "参考交易日历待核对"))
    if today["status"] != "READY" or not quote or not baseline:
        return wait(today["status"], today.get("label", "等待今日涨跌"), "需要有效当日报价及匹配的上一参考交易日净值")
    if fund.get("initialization_pending") or not groups or base.get("units") is None or tactical.get("units") is None:
        return wait("HOLDINGS_UNKNOWN", "持仓待补", "先完成当前持仓与底仓、T仓记录")
    if v2 and not risk["ready"]:
        return wait(risk["status"], "多日风险资料待补", "；".join(risk["reasons"]))

    nav = dec(quote["value"], positive=True)
    units = dec(fund.get("units", (fund.get("position_buckets") or {}).get("total_units", ZERO)))
    plan["holding_value"] = money(units * nav)
    plan["committed_holding"] = plan["holding_value"] + plan["pending_buy"]
    plan["headroom"] = max(ZERO, cap - plan["committed_holding"])
    if v2:
        plan["committed_holding"] = risk["capital_used"]
        plan["headroom"] = risk["capital_headroom"]
    plan["base_units"] = dec(base["units"])
    plan["base_value"] = money(plan["base_units"] * nav)
    plan["t_value"] = plan["holding_value"] - plan["base_value"]
    plan["t_available_units"] = dec(tactical.get("available_units", ZERO))
    plan["valuation_basis"] = "CURRENT_QUOTE"
    plan["valuation_date"] = quote.get("effective_date", market["date"])
    on = market["date"]
    tactical_orders = [o for o in own_orders if o.get("bucket") == "TACTICAL"]
    current_orders = [o for o in tactical_orders if stamp(o["submitted_at"]).astimezone(SHANGHAI).date().isoformat() == on]
    for side, key in (("BUY", "used_buy"), ("SELL", "used_sell")):
        plan[key] = sum((_committed(o, nav) for o in current_orders if o["side"] == side), ZERO)
    item["used_today"] = plan["used_buy"] + plan["used_sell"]
    plan["remaining_daily"] = max(ZERO, daily_budget - item["used_today"])

    paused = market["status"] == "BREAK" or source_lunch(now, market["scope"])
    cutoff = not market["before_cutoff"] or market["status"] == "CLOSED"
    partial = market.get("partial_holiday", False)
    item["preview_only"] = paused or cutoff or partial
    item["reference_label"] = ("今日已截止 · 收盘情景" if cutoff else "午间休市 · 按价情景" if paused else
                               "部分市场休市 · 按价情景" if partial else "当日涨跌阶梯")
    # ``today_quote`` computes Decimal ratios at working precision; those are
    # already validated domain values, not external 12-place decimal inputs.
    change = today["change"] if isinstance(today["change"], Decimal) else dec(today["change"], nonnegative=False)
    raw_target = target = max((tier["amount"] for tier in tiers if abs(change) >= dec(tier["trigger"])), default=ZERO)
    if v2:
        plan["raw_target_today"] = raw_target
        if change < ZERO:
            factor = {"NORMAL": Decimal("1"), "DEFENSIVE": Decimal(".5"), "PAUSED": ZERO}[risk["state"]]
            target = money(raw_target * factor)
    plan["target_today"] = item["single_budget"] = target
    item["trigger"] = next((dec(t["trigger"]) for t in reversed(tiers) if raw_target and raw_target == t["amount"]), None)
    if v2 and raw_target and change < ZERO and risk["state"] == "PAUSED":
        return wait("RISK_PAUSED", "暂停加仓", "；".join(risk["reasons"]), hold=True)
    if not target:
        reason = "当天涨跌未达到当前波动档位 h" if v2 else "当天涨跌不足 1%，未达到计划触发档位"
        return wait("HOLD", "继续持有", reason, hold=True)
    direction = "BUY" if change < ZERO else "SELL"
    opposite = "SELL" if direction == "BUY" else "BUY"
    opposite_today = plan["used_sell" if direction == "BUY" else "used_buy"]
    if opposite_today > ZERO or any(o["side"] == opposite and not o.get("closed") for o in tactical_orders):
        return wait("OPPOSITE_ORDER", "暂停反向参考", "当日已有相反方向做T申请，或历史反向申请尚未结束")
    used_same = plan["used_buy" if direction == "BUY" else "used_sell"]
    size = min(max(ZERO, target - used_same), plan["remaining_daily"])
    if size < Decimal("1"):
        return wait("BUDGET_USED", "当前档位额度已用", "已扣除当日有效做T申请及实际确认金额", hold=True)
    rule = fund["rules"]
    if not rule.get("verified") or rule.get("effective_from", "9999") > on or (rule.get("effective_to") and on >= rule["effective_to"]):
        return wait("RULES_UNVERIFIED", "费用待核实", "先核实费用规则及申请日期的有效范围")
    if not rule.get("subscriptions_open" if direction == "BUY" else "redemptions_open", True):
        return wait("PRODUCT_CLOSED", "此方向暂停申请", "产品当前暂停此方向申请")

    if direction == "BUY":
        if v2 and risk["cooldown_blocked"]:
            return wait("BUY_COOLDOWN", "补仓冷却中", "需隔完整参考交易日；最早恢复日期为 " + (risk["cooldown_until"] or "日历待补"), hold=True)
        if v2:
            size = min(size, risk["rolling_buy_remaining"])
            if size < Decimal("1"):
                return wait("ROLLING_BUY_LIMIT", "近5日买入额度已用", "只统计买入毛额，卖出与浮亏不会重置该额度", hold=True)
        size = min(size, plan["headroom"])
        if size < Decimal("1"):
            reason = ("市值与剩余经济成本取较高值，加待买已占用计划额度；浮亏不会释放补仓空间" if v2 else
                      "当前持仓和待确认买入已占用计划额度；不强制卖出")
            return wait("HOLDING_CAP", "已达计划仓位上限", reason, hold=True)
        # All inputs are cent amounts; round down defensively to honor the cap.
        size = size.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    else:
        step = dec(rule.get("unit_step", "0.01"), positive=True)
        prefix, physical_units = [], ZERO
        try:
            # Only a continuous physical FIFO prefix is eligible. A younger or
            # charged lot ends the prefix; it cannot be skipped for a later lot.
            # An unknown holding-date cannot safely be ordered after dated
            # lots. Check it first rather than inventing a later FIFO position.
            for lot in sorted(fund["lots"], key=lambda x: (x.get("acquired") or "", x["id"])):
                available = dec(lot["units"]) - dec(lot.get("reserved", ZERO))
                if available <= ZERO:
                    continue
                if not lot.get("available_from"):
                    if not prefix:
                        return wait("AVAILABILITY_UNKNOWN", "可卖日期待补", "最先卖出批次的可卖日期未知，不能跳过后计算FIFO免赎回费份额")
                    break
                if lot["available_from"] > on:
                    continue
                acquired = lot.get("acquired")
                if acquired is None:
                    if not prefix:
                        return wait("HISTORY_INCOMPLETE", "持有期待补", "缺少最先卖出批次的持有期起算日期")
                    break
                if (day(on) - day(acquired)).days < 30 or fee_rate(rule, acquired, on) != ZERO:
                    break
                prefix.append(lot)
                physical_units += available
            max_units = min(plan["t_available_units"], dec(fund.get("sellable_units", physical_units)), physical_units)
            max_steps = int((max_units / step).to_integral_value(rounding=ROUND_FLOOR))
            if max_steps <= 0:
                return wait("NO_FEE_FREE_T_UNITS", "暂无免赎回费T份额", "计划只参考可卖T仓及物理FIFO中持有满30天的连续免赎回费份额")
            low, high = 0, max_steps
            while low < high:
                mid = (low + high + 1) // 2
                candidate = redemption_preview(prefix, mid * step, nav, rule, on)
                if candidate["gross"] <= size:
                    low = mid
                else:
                    high = mid - 1
            if not low:
                return wait("UNITS_RESERVED", "可卖金额不足", "份额步长与逐批取整后不能形成当前参考金额")
            preview = redemption_preview(prefix, low * step, nav, rule, on)
            if preview["gross"] < Decimal("1"):
                return wait("UNITS_RESERVED", "可卖金额不足", "可卖出金额不足 1 元")
            impact = sell_impact(fund["position_buckets"], preview, nav, on, now,
                                 official_total=fund["position_buckets"].get("total_pnl"))
        except Problem as exc:
            return wait(exc.code, "卖出依据待核对", exc.message)
        if impact["status"] != "READY":
            return wait("PNL_UNKNOWN", "成本待补，暂停卖出参考", "成本未知时不虚构本笔预计实现收益")
        impact["basis"] = {k: quote.get(k) for k in ("id", "value_kind", "effective_date", "as_of", "observed_at", "source")}
        impact["buy_comparison"] = buy_price_comparison(fund, orders, preview, nav, on, now)
        impact["day_change"] = change
        profit_buffer = max(Decimal("2.00"), preview["gross"] * Decimal("0.005"))
        if impact["expected_realized_pnl"] < profit_buffer:
            item["candidate_sell_impact"] = impact
            item["profit_buffer"] = profit_buffer
            return wait("PROFIT_BUFFER", "暂不卖出", "本笔净已实现收益未达到 2 元或毛额 0.5% 中较高的缓冲", hold=True)
        item.update(units=preview["units"], fee=preview["fee"], net=preview["net"], sell_impact=impact,
                    profit_buffer=profit_buffer)
        size = preview["gross"]

    can_apply = market["status"] == "OPEN" and not item["preview_only"]
    action_label = "参考买入" if direction == "BUY" else "参考卖出"
    label = ("今日已截止 · " if cutoff else "午间情景 · " if paused else "休市情景 · " if partial else "") + action_label
    hint = ("仅展示按当前涨跌计算的情景，今日申请已截止" if cutoff else
            "仅展示按上午报价计算的情景，午间休市请勿按此直接申请" if paused else
            "部分市场休市，仅查看按价情景" if partial else
            "仅作本地参考；实际下单须在北京时间15:00前于渠道完成")
    return dict(item, action=direction, status="REFERENCE", label=label, amount=money(size),
                can_apply=can_apply, action_hint=hint,
                reason=("按波动阶梯、多日防御、近5日额度、冷却与成本占用计算；计划额度不是账户现金" if v2 else
                        "按当日涨跌阶梯，已扣减有效申请；计划额度不是账户现金"))
