"""Versioned T reference presets. Decimal heuristics, never brokerage orders.

Thresholds use empirical absolute daily NAV returns, not expected profits.
Budgets scale with the current marked holding. No ledger mutations here.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from app.domain.finance import ZERO, Problem, dec, money, stamp, SHANGHAI, redemption_preview
from app.domain.market_calendar import market_session
from app.domain.sell_impact import sell_impact
from app.domain.sell_comparison import buy_price_comparison
from app.domain.quote_phase import closing_reference, after_source_close, source_lunch, lunch_reference

METHOD_VERSION = "daily-t-2026-v2"
PRESETS = [
    {"level": "OBSERVE", "label": "观察", "description": "只看涨跌，不生成金额", "quantile": None, "fraction": "0", "fee_cap": "0"},
    {"level": "CAUTIOUS", "label": "谨慎", "description": "较大波动、小额参考", "quantile": 80, "fraction": "0.02", "fee_cap": "0"},
    {"level": "BALANCED", "label": "均衡", "description": "适中触发、适中金额", "quantile": 60, "fraction": "0.05", "fee_cap": "0.005"},
    {"level": "ACTIVE", "label": "积极", "description": "较易触发、较高额度", "quantile": 40, "fraction": "0.08", "fee_cap": "0.005"},
]


def profile_options(history, today, market_value, corporate_action=False):
    # Use strictly prior days. Today's move must not change its own threshold.
    prior = [r for r in history if r["effective_date"] < today][-61:]
    moves = sorted(abs(dec(b["value"]) / dec(a["value"], positive=True) - 1) for a, b in zip(prior, prior[1:]))
    ready = len(moves) >= 20 and not corporate_action
    options = []
    for p in PRESETS:
        trigger = None
        if ready and p["quantile"]:
            rank = (len(moves) * p["quantile"] + 99) // 100
            raw = max(moves[rank - 1], Decimal("0.005"), dec(p["fee_cap"]) * 2)
            trigger = (raw / Decimal("0.001")).to_integral_value(rounding=ROUND_CEILING) * Decimal("0.001")
        budget = money(dec(market_value) * dec(p["fraction"])) if market_value is not None else None
        options.append(dict(p, trigger=trigger, single_budget=budget,
                            daily_budget=budget * 2 if budget is not None else None))
    return {"method_version": METHOD_VERSION, "sample_count": len(moves), "lookback": 60,
            "first_date": prior[0]["effective_date"] if prior else None,
            "last_date": prior[-1]["effective_date"] if prior else None,
            "input_ids": [r["id"] for r in prior], "ready": ready,
            "blocked_by": "CORPORATE_ACTION_REVIEW" if corporate_action else (None if ready else "HISTORY_SHORT"),
            "options": options}


def fixed_gold_reference(row, today):
    p = row.get('provenance') or {}
    return (row.get('value_kind') == 'LOCAL_ESTIMATE' and row.get('quality') == 'LOCAL_GOLD_REFERENCE'
            and p.get('provider_id') == 'SGE_GOLD_REFERENCE' and p.get('model_version') == 'shau-gold-v1'
            and p.get('reference_mode') == 'TODAY_PM_FIXING' and p.get('fixed_for_date') is True
            and p.get('fixing_date') == today and row.get('as_of') is None)


def today_quote(history, estimates, now, session, corporate_action=False):
    today = session["date"]
    closed = session['status'] not in {'NOT_OPEN', 'UNKNOWN'} and after_source_close(now, session['scope'])
    paused = source_lunch(now, session['scope'])
    result = {"change": None, "quote": None, "baseline": None, "status": "MISSING",
              "label": "收盘数据待更新" if closed else "待更新",
              "display_title": "收盘涨跌" if closed else "午间涨跌" if paused else "今日涨跌", "phase": "WAIT"}
    if session["status"] in {"NOT_OPEN", "UNKNOWN"}:
        return dict(result, status=session["status"], label=session["label"])
    if corporate_action:
        return dict(result, status="CORPORATE_ACTION_REVIEW", label="口径待核对")
    prior = [r for r in history if r["effective_date"] < today]
    baseline = prior[-1] if prior else None
    if not baseline or baseline["effective_date"] != session["previous_date"]:
        return dict(result, status="BASELINE_MISSING", label="基准待更新")
    result["baseline"] = baseline
    official = [r for r in history if r["effective_date"] == today and stamp(r["observed_at"]) <= stamp(now) and
                (not r.get("as_of") or stamp(r["as_of"]) <= stamp(now))]
    eligible = [r for r in estimates if r["effective_date"] == today and
                stamp(r.get("as_of") or r["observed_at"]).astimezone(SHANGHAI).date().isoformat() == today and
                stamp(r["observed_at"]) <= stamp(now) and
                (0 <= (stamp(now) - stamp(r.get("as_of") or r["observed_at"])).total_seconds() <= 900 or
                 (closing_reference(r, today) and stamp(r['as_of']) <= stamp(now)) or
                 lunch_reference(r, now, session['scope']) or
                 (session['scope'] == 'GOLD_CN' and fixed_gold_reference(r, today)))]
    matching = [r for r in eligible if not r.get("provenance") or
                (r["provenance"].get("reference_date") == baseline["effective_date"] and
                 dec(r["provenance"]["reference_nav"]) == dec(baseline["value"]))]
    if eligible and not matching and not official:
        return dict(result, status="QUOTE_BASIS_MISMATCH", label="估值基准待核对")
    quote = official[-1] if official else (max(matching, key=lambda r: (stamp(r.get("as_of") or r["observed_at"]), stamp(r['observed_at']))) if matching else None)
    if quote:
        closing = closing_reference(quote, today)
        morning = paused and quote['value_kind'] != 'OFFICIAL_NAV'
        label = (('收盘正式涨跌' if closed else '今日正式涨跌') if quote['value_kind'] == 'OFFICIAL_NAV' else
                 '15:00 收盘估算' if closing and session['scope'] != 'GOLD_CN' else
                 '金价收盘参考估算' if closing else
                 '上海金午盘参考估算' if fixed_gold_reference(quote, today) else
                 '午间休市 · 上午最后估算' if morning else
                 '收盘前最后估算' if closed else
                 '金价参考估算' if quote.get('quality') == 'LOCAL_GOLD_REFERENCE' else '今日估算涨跌')
        return dict(result, status="READY", label=label,
                    quote=quote, phase='OFFICIAL' if quote['value_kind'] == 'OFFICIAL_NAV' else 'CLOSE' if closing or (closed and fixed_gold_reference(quote, today)) else 'BREAK' if morning else 'LIVE',
                    change=dec(quote["value"], positive=True) / dec(baseline["value"], positive=True) - 1)
    stale = any(r["effective_date"] == today and stamp(r.get("as_of") or r["observed_at"]) <= stamp(now) for r in estimates)
    return dict(result, status="STALE" if stale else "MISSING",
                label='收盘数据待更新' if closed else "报价已过期" if stale else "待更新")


def daily_context(fund, profile, estimates, now, scope, corporate_action=False):
    session = market_session(now, scope)
    history = [r for r in fund["history"] if stamp(r["observed_at"]) <= stamp(now)]
    options = profile_options(history, session["date"], fund["market_value"], corporate_action)
    quote = today_quote(history, estimates, now, session, corporate_action)
    return {"market": session, "today": quote, "profile": profile, "presets": options}


def reference(fund, orders, now):
    context = fund["daily_t"]
    profile, market, quote, presets = (context[k] for k in ("profile", "market", "today", "presets"))
    option = next(p for p in presets["options"] if p["level"] == profile["level"])
    item = {"fund_id": fund["id"], "level": profile["level"], "profile_version": profile["_version"],
            "method_version": METHOD_VERSION, "action": "WAIT", "amount": None, "units": None, "fee": None,
            "status": "WAIT", "label": "等待今日涨跌", "reason": "", "cash_status": "REFERENCE_ONLY",
            "observation_id": (quote["quote"] or {}).get("id"),
            "baseline_id": (quote["baseline"] or {}).get("id"), "trigger": option["trigger"],
            "single_budget": option["single_budget"], "daily_budget": option["daily_budget"], "used_today": ZERO}
    def wait(status, label, reason):
        return dict(item, status=status, label=label, reason=reason)
    if market["status"] in {"NOT_OPEN", "UNKNOWN", "BREAK", "CLOSED"}:
        return wait(market["status"], market["label"], market["detail"])
    if source_lunch(now, market['scope']):
        return wait('BREAK', '午间休市', '估值来源北京时间13:00恢复，休市报价仅供查看')
    if not market["before_cutoff"]:
        return wait("CUTOFF", "今日申请已截止", "北京时间 15:00 后的参考报价不生成今日金额")
    if profile["level"] == "OBSERVE":
        return wait("OBSERVE", "仅观察", "选择做 T 强度后生成参考金额")
    if market["partial_holiday"]:
        return wait("PARTIAL_HOLIDAY", "部分市场休市", "跨市场基金暂停金额参考")
    if quote["status"] != "READY":
        return wait(quote["status"], "等待今日涨跌", "需要当天且 15 分钟内的报价及上一参考交易日净值")
    if not presets["ready"]:
        return wait(presets["blocked_by"], "样本或口径待补", "需至少 20 个历史涨跌样本，分红拆分需先核对")
    if fund.get("initialization_pending") or option["single_budget"] is None:
        return wait("HOLDINGS_UNKNOWN", "持仓待补", "先完成当前持仓记录")
    trigger, change = option["trigger"], quote["change"]
    direction = "BUY" if change <= -trigger else "SELL" if change >= trigger else None
    if direction is None:
        return dict(item, action="HOLD", status="HOLD", label="继续持有", amount=ZERO,
                    reason="当天涨跌尚未达到当前档位的触发幅度")
    own_orders = [o for o in orders if o["fund_id"] == fund["id"]]
    if any(not o["closed"] for o in own_orders):
        return wait("ORDER_PENDING", "先处理未结束申请", "避免同一报价重复生成金额")
    rule = fund["rules"]
    if not rule.get("verified") or rule.get("effective_from", "9999") > market["date"] or (rule.get("effective_to") and market["date"] >= rule["effective_to"]):
        return wait("RULES_UNVERIFIED", "费用待核实", "先核实费用规则与生效日期")
    if not rule.get("subscriptions_open" if direction == "BUY" else "redemptions_open", True):
        return wait("PRODUCT_CLOSED", "此方向暂停申请", "产品规则当前暂停此方向")
    nav = dec(quote["quote"]["value"], positive=True)
    # Count confirmed T turnover in both directions, including partially filled canceled orders.
    used = sum((dec(o["confirmed_gross"]) for o in own_orders if o.get("bucket") == "TACTICAL" and
                stamp(o["submitted_at"]).astimezone(SHANGHAI).date().isoformat() == market["date"]), ZERO)
    item["used_today"] = used
    size = min(option["single_budget"], max(ZERO, option["daily_budget"] - used))
    if size < 1:
        return wait("BUDGET_USED", "今日参考额度已用完", "按当天已确认的做 T 交易总额扣减")
    if direction == "SELL":
        step = dec(rule.get("unit_step", "0.01"), positive=True)
        units = min(dec(fund["sellable_units"]), (size / nav / step).to_integral_value(rounding=ROUND_FLOOR) * step)
        units = (units / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if units <= 0:
            return wait("UNITS_RESERVED", "暂无可卖份额", "已扣除冻结与尚不可卖出的份额")
        try:
            preview = redemption_preview(fund["lots"], units, nav, rule, market["date"])
            if preview["gross"] > size:
                # Per-lot cent rounding can exceed units * NAV. Search whole steps
                # against the actual rounded gross to keep the money cap strict.
                low, high = 0, int(units / step)
                while low < high:
                    mid = (low + high + 1) // 2
                    candidate = redemption_preview(fund["lots"], mid * step, nav, rule, market["date"])
                    if candidate["gross"] <= size:
                        low = mid
                    else:
                        high = mid - 1
                if not low:
                    return wait("UNITS_RESERVED", "可卖金额不足", "整份额步长与分批取整后超出参考额度")
                units = low * step
                preview = redemption_preview(fund["lots"], units, nav, rule, market["date"])
        except Problem as exc:
            return wait(exc.code, "费用暂不可估", exc.message)
        # Use unrounded tier rates: rounding tiny fees to zero must not bypass the cap.
        if any(dec(a["rate"]) > dec(option["fee_cap"]) for a in preview["allocations"]):
            return wait("FEE_LIMIT", "持有期费用较高", "FIFO 最先卖出的批次费率超过当前档位上限")
        if preview["gross"] < 1:
            return wait("UNITS_RESERVED", "可卖金额不足", "可卖出金额不足 1 元")
        try:
            impact = sell_impact(fund.get('position_buckets'), preview, nav, market['date'], now,
                                 official_total=(fund.get('position_buckets') or {}).get('total_pnl'))
        except Problem as exc:
            return wait(exc.code, '预期收益待核对', exc.message)
        if impact['status'] != 'READY':
            return wait('PNL_UNKNOWN', '成本待补，暂停卖出参考', '无法核对成本时不生成虚构的预计收益')
        impact['basis'] = {k: quote['quote'].get(k) for k in ('id', 'value_kind', 'effective_date', 'as_of', 'observed_at', 'source')}
        impact['buy_comparison'] = buy_price_comparison(fund, orders, preview, nav, market['date'], now)
        impact['day_change'] = change
        item['sell_impact'] = impact
        item.update(units=units, fee=preview["fee"], net=preview["net"])
        size = preview["gross"]
    return dict(item, action=direction, status="REFERENCE", label="参考买入" if direction == "BUY" else "参考卖出",
                amount=money(size), reason="按今日涨跌触发；参考金额不代表渠道成交价或可用余额")
