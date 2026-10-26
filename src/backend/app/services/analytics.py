from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
import json

from app.domain.finance import (ZERO, SHANGHAI, Problem, dec, money, amount, stamp, day, require,
                                trade_date, purchase_preview, redemption_preview)
from app.persistence.store import Store, pack, digest


class AnalyticsMixin:
    def save_strategy(self, fund_id, p):
        with self.store.transaction() as c:
            self.fund(c, fund_id)
            old = Store.doc(c, "strategy", fund_id, {"_version": 0})
            require(p.get("type") in {"TARGET_WEIGHT", "ANCHORED_THRESHOLD", "MANUAL_BUCKETS"}, "INVALID_STRATEGY", "请选择底仓/T记录、目标仓位或锚点规则")
            params = dict(p.get("params", {}))
            for input_name, field in [("target_percent", "target_weight"), ("band_percent", "band"), ("buy_percent", "buy_trigger"), ("sell_percent", "sell_trigger"), ("max_fee_percent", "max_fee_rate")]:
                if input_name in params:
                    params[field] = str(dec(params.pop(input_name)) / 100)
            required = ["single_amount", "daily_limit", "cash_reserve", "max_value", "min_value", "min_order"]
            for field in required:
                amount(params.get(field, "0"))
            require(p['type']=='MANUAL_BUCKETS' or dec(params.get("max_value", "0")) >= dec(params.get("min_value", "0")), "INVALID_STRATEGY", "最高持仓金额应不低于保留底仓")
            if p['type']=='MANUAL_BUCKETS':
                require(fund_id in self.state(c)['tracking'], 'TRACKING_REQUIRED', '请先建立底仓/T跟踪起点')
            for field in ["target_weight", "band", "buy_trigger", "sell_trigger", "max_fee_rate"]:
                require(dec(params.get(field, "0")) <= 1, "INVALID_STRATEGY", "比例字段应在 0 到 1 之间")
            if p["type"] == "ANCHORED_THRESHOLD":
                dec(params.get("anchor_nav", "0"), positive=True)
            require(isinstance(params.get("cooldown_days", 0), int) and params.get("cooldown_days", 0) >= 0,
                    "INVALID_STRATEGY", "冷却天数须为非负整数")
            clean = {"fund_id": fund_id, "enabled": p.get("enabled", False), "type": p["type"], "params": params,
                     "anchor_seq": self.state(c)["revision"], "updated_at": self.now()}
            saved = Store.put(c, "strategy", fund_id, clean, p.get("expected_version", 0 if old["_version"] == 0 else None))
            Store.put(c, "strategy_history", fund_id + ":" + str(saved["_version"]), saved)
            return saved

    def recommendations(self, *, persist=True):
        from app.domain import investment_plan as investment_plan_domain
        with (self.store.transaction() if persist else self.store.connect()) as c:
            if not persist:
                c.execute("BEGIN")
            snapshot = self.snapshot(c)
            state = self.state(c)
            funds = snapshot["funds"]
            now, today = stamp(self.now()), self.today()
            risk_history = {}
            for fund in funds:
                profile = fund["investment_plan_profile"]
                if not profile.get("enabled") or profile.get("template") != "DRAWDOWN_GUARD_V2":
                    continue
                # The V2 regime uses past formal observations, not just the last
                # NAV. A correction to an older point must invalidate a plan in
                # the same minute. Repeated receipts do not change this basis.
                visible = [row for row in fund.get("history", [])
                           if row.get("value_kind") == "OFFICIAL_NAV" and
                           row["effective_date"] <= today and stamp(row["observed_at"]) <= now and
                           (not row.get("as_of") or stamp(row["as_of"]) <= now)][-181:]
                source = fund.get("nav_sync") or {}
                risk_history[fund["id"]] = digest({
                    "history": [{key: row.get(key) for key in (
                        "id", "effective_date", "value", "value_kind", "quality",
                        "observed_at", "published_at", "as_of", "corporate_actions",
                        "corporate_action", "adjustment_factor")} for row in visible],
                    "corporate_actions": source.get("corporate_actions"),
                    "corporate_actions_complete": source.get("corporate_actions_complete"),
                })
            signature = {"revision": state["revision"], "date": today, "mode": self.mode,
                         "sell_comparison_version": "buy-price-comparison-v1",
                         "funds": [(f["id"], f["nav"]["id"] if f["nav"] else None,
                                    f["estimate"]["id"] if f["estimate"] else None,
                                    f["strategy"]["_version"], f["rules"]["_version"],
                                    f["daily_t"]["profile"]["_version"],
                                    f["investment_plan_profile"], investment_plan_domain.METHOD_VERSION,
                                    f["daily_t"]["presets"], f["daily_t"]["market"],
                                    f["daily_t"]["today"]) for f in funds]}
            if risk_history:
                signature["investment_plan_risk_history"] = risk_history
            # Include the availability window, so a plan cannot survive the cutoff.
            signature["before_cutoff"] = now.astimezone(SHANGHAI).time() < time(15, 0)
            signature["evaluation_minute"] = now.strftime("%Y%m%d%H%M")
            id = "plan_" + digest(signature)[:24]
            old = c.execute("SELECT payload FROM plans WHERE id=?", (id,)).fetchone()
            if old:
                stored = json.loads(old[0])
                if stamp(stored["expires_at"]) > now:
                    return stored
            reserve_cash = max((dec(f["strategy"].get("params", {}).get("cash_reserve", "0"))
                                for f in funds if f["strategy"].get("enabled")), default=ZERO)
            cash_left = max(ZERO, state["available_cash"] - reserve_cash)
            gold_exposure = sum(((f["market_value"] or ZERO) for f in funds if f.get("instrument_type") == "GOLD_LINKED"), ZERO)
            gold_ids = {f["id"] for f in funds if f.get("instrument_type") == "GOLD_LINKED"}
            gold_exposure += sum((o["requested"] - o["confirmed_gross"] for o in state["orders"].values()
                                  if o["fund_id"] in gold_ids and o["side"] == "BUY" and not o["closed"]), ZERO)
            result = []
            deadlines = [datetime.combine(day(today), time.fromisoformat(f["rules"].get("cutoff", "15:00:00")), SHANGHAI)
                         for f in funds if f["strategy"].get("enabled") and f['strategy'].get('type')!='MANUAL_BUCKETS']
            expires = min([now + timedelta(seconds=60)] + deadlines).isoformat()
            for f in funds:
                cfg, rule = f["strategy"], f["rules"]
                p = cfg.get("params", {})
                item = {"fund_id": f["id"], "fund_name": f["name"], "action": "WAIT", "amount": ZERO, "units": ZERO,
                        "fee": ZERO, "reasons": [], "blocked_by": [], "config_version": cfg["_version"],
                        "rule_version": rule["_version"], "value_kind": None}
                try:
                    require(cfg.get("enabled"), "OBSERVE_ONLY", "当前为观察模式，尚未启用个人规则")
                    if cfg.get('type')=='MANUAL_BUCKETS':
                        require(f.get('tracking') and f['position_buckets'].get('groups'), 'TRACKING_REQUIRED', '需要有效的底仓/T跟踪起点')
                        groups=f['position_buckets']['groups']
                        item.update(action='HOLD',tracking_mode=True,base_value=groups['BASE']['value'],t_value=groups['TACTICAL']['value'])
                        item['reasons']=['底仓与做T跟踪已启用，后续买卖直接选择归属',
                            '本次按现有持仓划分；未设涨跌触发条件，不生成金额买卖建议']
                        result.append(item)
                        continue
                    require(not f.get("initialization_pending"), "STATEMENT_UNRECONCILED", "截图历史尚未完成持仓及到账对账，暂停金额建议")
                    require(rule.get("verified") and rule.get("effective_from", "9999") <= today and
                            (not rule.get("effective_to") or today < rule["effective_to"]), "RULES_UNVERIFIED", "产品及渠道规则未核实或不在生效区间")
                    accepted = trade_date(self.now(), rule)
                    item["trade_date"] = accepted
                    require(accepted == today, "AFTER_CUTOFF", "本次申请已属于后续开放日；当前只作观察，届时重新计算")
                    quote = f["estimate"] or f["nav"]
                    require(quote is not None, "DATA_MISSING", "没有可用净值或估值")
                    if quote["value_kind"] != "OFFICIAL_NAV":
                        reference = quote.get("as_of") or quote["observed_at"]
                        require((now - stamp(reference)).total_seconds() <= 900, "DATA_STALE", "盘中估值已超过 15 分钟，暂停金额建议")
                    else:
                        prior_dates = [d for d in rule.get("open_dates", []) if d < today]
                        require(not prior_dates or quote["effective_date"] >= prior_dates[-1], "DATA_STALE", "正式净值尚未覆盖上一已核实开放日")
                    nav = dec(quote["value"], positive=True)
                    for split in state["splits"]:
                        if split["fund_id"] == f["id"] and quote["id"] in split.get("affected_observation_ids", []):
                            nav /= dec(split["factor"])
                    item.update(signal_nav=nav, value_kind=quote["value_kind"], observation_id=quote["id"], nav_date=quote["effective_date"])
                    require(snapshot["portfolio"]["equity"] is not None, "DATA_MISSING", "组合有持仓缺少可靠净值，无法计算仓位")
                    pending = [o for o in state["orders"].values() if o["fund_id"] == f["id"] and not o["closed"]]
                    require(not pending, "ORDER_PENDING", "该基金仍有未结束申请，先补充确认或取消结果")
                    linked = [o for o in state["orders"].values() if o["fund_id"] == f["id"] and o.get("strategy_id") == f["id"]
                              and o.get("strategy_version", cfg["_version"]) == cfg["_version"] and o["closed"] and o["confirmations"]]
                    cooldown = p.get("cooldown_days", 0)
                    if linked and cooldown:
                        last_day = max(o["confirmations"][-1].get("confirm_date", today) for o in linked)
                        require((day(today) - day(last_day)).days >= cooldown, "COOLDOWN", "仍在所设冷却期内")
                    direction, desired = None, ZERO
                    value = f["units"] * nav
                    if cfg["type"] == "TARGET_WEIGHT":
                        target = snapshot["portfolio"]["equity"] * dec(p.get("target_weight", "0"))
                        band = snapshot["portfolio"]["equity"] * dec(p.get("band", "0"))
                        if value < target - band:
                            direction, desired = "BUY", target - value
                        elif value > target + band:
                            direction, desired = "SELL", value - target
                        item["reasons"].append("按目标仓位区间与当前确认资产计算")
                    else:
                        anchor = dec(p["anchor_nav"], positive=True)
                        newer = [o for o in linked if any(x["event_id"] for x in o["confirmations"])
                                 and max(next(e["seq"] for e in state["audit"] if e["id"] == x["event_id"]) for x in o["confirmations"]) > cfg.get("anchor_seq", 0)]
                        anchor_seq = cfg.get("anchor_seq", 0)
                        if newer:
                            o = max(newer, key=lambda x: max(next(e["seq"] for e in state["audit"] if e["id"] == z["event_id"]) for z in x["confirmations"]))
                            anchor = sum((dec(x["nav"]) * dec(x["units"]) for x in o["confirmations"]), ZERO) / o["confirmed_units"]
                            anchor_seq = max(next(e["seq"] for e in state["audit"] if e["id"] == x["event_id"]) for x in o["confirmations"])
                        for split in state["splits"]:
                            if split["fund_id"] == f["id"] and split["seq"] > anchor_seq:
                                anchor /= dec(split["factor"])
                        change = nav / anchor - 1
                        item["anchor_nav"] = anchor
                        if change <= -dec(p.get("buy_trigger", "0.02")):
                            direction = "BUY"
                        elif change >= dec(p.get("sell_trigger", "0.02")):
                            direction = "SELL"
                        desired = dec(p.get("single_amount", "0"))
                        item["reasons"].append("相对固定锚点变化 " + format(change * 100, ".2f") + "%")
                    if direction is None:
                        item["action"] = "HOLD"
                        item["reasons"].append("未触发当前规则，保留现有仓位")
                    else:
                        completed_today = [o for o in state["orders"].values() if o["fund_id"] == f["id"] and
                                           stamp(o["submitted_at"]).astimezone(SHANGHAI).date().isoformat() == today and
                                           o["confirmed_units"] > 0]
                        require(not any(o["side"] != direction for o in completed_today), "OPPOSITE_DIRECTION_TODAY", "本交易日已有相反方向的实际确认，暂停反向规则计划")
                        require(not any(o.get("strategy_id") == f["id"] for o in completed_today), "DAILY_SIGNAL_CONSUMED", "本交易日已完成关联规则操作，等待下一开放日重新评估")
                        require(rule.get("subscriptions_open" if direction == "BUY" else "redemptions_open", True), "PRODUCT_CLOSED", "产品当前暂停此方向申请")
                        used = ZERO
                        for order in state["orders"].values():
                            if order["fund_id"] == f["id"] and stamp(order["submitted_at"]).astimezone(SHANGHAI).date().isoformat() == today:
                                used += order["requested"] if order["side"] == "BUY" else order["requested"] * nav
                        daily_left = max(ZERO, dec(p.get("daily_limit", "0")) - used)
                        size = min(desired, dec(p.get("single_amount", "0")), daily_left)
                        if direction == "BUY":
                            size = money(min(size, cash_left, max(ZERO, dec(p.get("max_value", "0")) - value)))
                            group = f.get("instrument_type")
                            if group == "GOLD_LINKED" and p.get("group_limit"):
                                size = min(size, max(ZERO, dec(p["group_limit"]) - gold_exposure))
                            require(size >= dec(p.get("min_order", "1")), "INSUFFICIENT_SETTLED_CASH", "现金、单日额度或最高仓位不足以形成最小申请")
                            preview = purchase_preview(size, nav, rule)
                            cash_left -= size
                            if group == "GOLD_LINKED":
                                gold_exposure += size
                        else:
                            require(not f["incomplete"], "HISTORY_INCOMPLETE", "批次成本或持有日期不完整，不能准确预估卖出费用")
                            size = min(size, max(ZERO, value - dec(p.get("min_value", "0"))))
                            step = dec(rule.get("unit_step", "0.01"))
                            units = min(f["sellable_units"], (size / nav // step) * step)
                            require(units > 0 and size >= dec(p.get("min_order", "1")), "UNITS_RESERVED", "底仓或可赎回份额限制，当前没有可执行数量")
                            preview = redemption_preview(f["lots"], units, nav, rule, today)
                            require(not preview["gross"] or preview["fee"] / preview["gross"] <= dec(p.get("max_fee_rate", "1")), "FEE_LIMIT", "预计赎回费用超过当前设置上限")
                        item.update(action=direction, amount=preview["gross"], units=preview["units"], fee=preview["fee"], net=preview["net"])
                        item["reasons"].append("已应用费用、底仓、单笔/日额度及组合现金限制；金额为估计")
                except Problem as exc:
                    item["action"] = "WAIT" if exc.code in {"OBSERVE_ONLY", "AFTER_CUTOFF", "ORDER_PENDING", "COOLDOWN", "OPPOSITE_DIRECTION_TODAY", "DAILY_SIGNAL_CONSUMED"} else "BLOCKED"
                    item["blocked_by"].append(exc.code)
                    item["reasons"].append(exc.message)
                result.append(item)
            payload = {"id": id, "mode": self.mode, "ledger_version": state["revision"], "generated_at": self.now(),
                       "expires_at": expires, "snapshot": signature, "items": result, "cash_reserve": reserve_cash,
                       "remaining_budget": cash_left, "daily_t_items": self.daily_t_references(snapshot)}
            if persist:
                c.execute("INSERT OR IGNORE INTO plans VALUES(?,?,?)", (id, pack(payload), self.now()))
            return payload

    def prediction_data(self, c, fund_id):
        state = self.state(c)
        source = Store.doc(c, "nav_sync", fund_id)
        require(not source or not source.get("corporate_actions"), "CORPORATE_ACTION_REVIEW",
                "公开来源含分红或拆分提示，历史序列口径需先核对；当前暂停基线评估和预测")
        require(not source or source.get("corporate_actions_complete") is not False, "CORPORATE_ACTION_REVIEW",
                "备用日净值来源没有完整分红拆分信息，先核对历史序列口径")
        require(not any(e["payload"].get("fund_id") == fund_id and not e["reversed"] and
                        e["kind"] in {"SPLIT", "DIVIDEND_DECLARED"} for e in state["audit"]),
                "CORPORATE_ACTION_REVIEW", "该基金已有分红或拆分事件，请先核对历史序列的复权/单位口径；当前暂停基线评估和预测")
        records = self.observations(c, fund_id, "OFFICIAL_NAV")
        return [r for r in records if stamp(r["observed_at"]) <= stamp(self.now())]

    def evaluate_prediction(self, fund_id, method="LAST_VALUE"):
        with self.store.connect() as c:
            records = self.prediction_data(c, fund_id)
        require(method in {"LAST_VALUE", "MEAN_RETURN_5"}, "INVALID_MODEL", "请选择上一有效值或五期平均收益基线")
        folds = []
        for index in range(20, len(records)):
            train, target = records[:index], records[index]
            issue = stamp(train[-1]["observed_at"])
            eligible = all(stamp(r["observed_at"]) <= issue for r in train)
            if not eligible or issue >= stamp(target["observed_at"]) or issue.astimezone(SHANGHAI).date() > day(target["effective_date"]):
                continue
            estimate = self.baseline(train, method)
            actual = dec(target["value"])
            folds.append({"date": target["effective_date"], "issue_at": issue.isoformat(), "predicted": estimate,
                          "actual": actual, "absolute_error": abs(estimate - actual), "percentage_error": abs(estimate - actual) / actual})
        sufficient = len(folds) >= 5
        return {"fund_id": fund_id, "method": method, "mode": self.mode, "sample_count": len(records), "fold_count": len(folds),
                "status": "DEMO_EVALUATION" if sufficient and self.mode == "demo" else "RESEARCH_ONLY" if sufficient else "INSUFFICIENT_TIME_EVIDENCE",
                "mae": sum((f["absolute_error"] for f in folds), ZERO) / len(folds) if sufficient else None,
                "mape": sum((f["percentage_error"] for f in folds), ZERO) / len(folds) if sufficient else None,
                "folds": folds, "notice": "按披露/首见时间推进的样本外基线评估。合成演示结果不代表真实预测效果；未验证交易收益。"}

    @staticmethod
    def baseline(records, method, steps=1):
        last = dec(records[-1]["value"])
        if method == "LAST_VALUE":
            return last
        recent = records[-6:]
        returns = [dec(b["value"]) / dec(a["value"]) - 1 for a, b in zip(recent, recent[1:])]
        return (last * (1 + sum(returns, ZERO) / len(returns)) ** steps).quantize(Decimal("0.000001"))

    def predict(self, p, c=None):
        if c is None:
            with self.store.transaction() as connection:
                return self.predict(p, connection)
        self.fund(c, p["fund_id"])
        id = "pred_" + digest(p["key"])[:24]
        old = c.execute("SELECT payload FROM predictions WHERE id=?", (id,)).fetchone()
        if old:
            prior = json.loads(old[0])
            require(prior.get("request_hash") == digest(p), "IDEMPOTENCY_CONFLICT", "相同幂等键已用于不同预测内容")
            return prior
        records = self.prediction_data(c, p["fund_id"])
        require(len(records) >= 20, "DATA_INSUFFICIENT", "至少需要 20 个有效的历史正式净值点，才能创建基线预测")
        rule = self.rules(c, p["fund_id"])
        previous_dates = [d for d in rule.get("open_dates", []) if d < self.today()]
        require((not rule.get("verified") or not previous_dates or records[-1]["effective_date"] >= previous_dates[-1]) and
                (day(self.today()) - day(records[-1]["effective_date"])).days <= 14, "DATA_STALE", "最近正式净值已过期，请先更新历史数据再创建预测")
        target = day(p["target_date"])
        require(target > day(records[-1]["effective_date"]) and target >= day(self.today()), "INVALID_TARGET_DATE", "目标日期应晚于最近净值日，且不能早于当前业务日")
        method = p.get("method", "LAST_VALUE")
        require(method in {"LAST_VALUE", "MEAN_RETURN_5", "EXTERNAL"}, "INVALID_MODEL", "未知预测方法")
        known_horizon = rule.get("verified") and p["target_date"] in rule.get("open_dates", [])
        steps = len([d for d in rule.get("open_dates", []) if records[-1]["effective_date"] < d <= p["target_date"]]) if known_horizon else None
        require(method != "MEAN_RETURN_5" or known_horizon, "CALENDAR_MISSING", "均值延伸基线需要已核实且覆盖目标日期的开放日历")
        generated = stamp(p.get("generated_at") or self.now()).isoformat()
        require(stamp(generated) <= stamp(self.now()), "INVALID_TIME", "预测生成时间不能在未来")
        eligible = [r for r in records if stamp(r["observed_at"]) <= stamp(generated)]
        require(len(eligible) >= 20, "DATA_INSUFFICIENT", "预测生成时点之前可见的历史样本不足 20 个")
        predicted = dec(p["predicted_nav"], positive=True) if method == "EXTERNAL" else self.baseline(eligible, method, steps or 1)
        horizon = steps or 1
        require(len(eligible) > horizon + 5, "DATA_INSUFFICIENT", "目标期限过长，历史样本不足以构造同期限情景带")
        recent = eligible[-(20 + horizon):]
        moves = sorted(abs(dec(recent[i]["value"]) / dec(recent[i-horizon]["value"]) - 1) for i in range(horizon, len(recent)))
        spread = moves[min(len(moves) - 1, int(len(moves) * .9))] if known_horizon else None
        payload = {"id": id, "fund_id": p["fund_id"], "method": method, "mode": self.mode,
                   "target_date": target.isoformat(), "generated_at": generated, "recorded_at": self.now(),
                   "predicted_nav": predicted, "lower_scenario": predicted * max(ZERO, 1 - spread) if spread is not None else None,
                   "upper_scenario": predicted * (1 + spread) if spread is not None else None, "sample_count": len(eligible), "horizon_steps": steps,
                   "source": p.get("source", "本机研究基线"), "input_ids": [r["id"] for r in eligible],
                   "input_hash": digest(eligible), "request_hash": digest(p), "data_cutoff": eligible[-1]["observed_at"], "version": "baseline-v1.1",
                   "notice": "历史变动情景带，非置信区间；仅研究/观察，不是成交净值或保证。"}
        c.execute("INSERT INTO predictions VALUES(?,?,?)", (id, pack(payload), self.now()))
        self.resolve_predictions(c, p["fund_id"])
        return payload

    def prediction_list(self):
        with self.store.connect() as c:
            return [dict(json.loads(r["payload"]), resolution=json.loads(r["resolution"]) if r["resolution"] else None)
                    for r in c.execute("SELECT p.payload,r.payload AS resolution FROM predictions p LEFT JOIN resolutions r ON p.id=r.prediction_id ORDER BY p.created_at DESC,p.rowid DESC LIMIT 200")]
