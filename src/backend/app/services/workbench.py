from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json
import re

from app.domain.finance import (ZERO, Problem, amount, dec, money, day, stamp, require,
                                SHANGHAI, purchase_preview, redemption_preview, solve_net_target)
from app.domain.ledger import project
from app.persistence.store import Store, digest, pack, utcnow
from app.services.analytics import AnalyticsMixin
from app.services.imports import ImportMixin
from app.services.backup import BackupMixin
from app.services.statements import StatementMixin
from app.services.initialization import InitializationMixin
from app.services.daily_t import DailyTMixin
from app.services.market_data import MarketDataMixin
from app.services.storage import StorageMixin
from app.services.asset_imports import AssetImportMixin
from app.services.sell_preview import SellPreviewMixin
from app.services.fund_sidebar import FundSidebarMixin
from app.services.research import ResearchMixin
from app.services.order_automation import OrderAutomationMixin
from app.services.forecasting import ForecastingMixin
from app.services.reconciliation import ReconciliationMixin
from app.services.t_review import TReviewMixin
from app.services.maintenance import MaintenanceMixin
from app.services.valuation_explanation import ValuationExplanationMixin
from app.services.portfolio_risk import PortfolioRiskMixin
from app.services.forecast_scorecard import ForecastScorecardMixin
from app.services.plan_scenarios import PlanScenariosMixin
from app.domain.order_automation import AUTO_BASIS, submission_time
from app.domain.overview import fund_day_valuation, portfolio_day_valuation
from app.domain.return_valuation import fund_return_valuation, portfolio_return_valuation
from app.domain.holding_composition import holding_composition
from app.providers.public_nav import SUPPORTED

VALUE_KINDS = {"OFFICIAL_NAV", "THIRD_PARTY_ESTIMATE", "LOCAL_ESTIMATE", "ETF_MARKET_PRICE", "ETF_IOPV", "BENCHMARK_PRICE", "MANUAL_VALUE"}
EVENT_KINDS = {"CASH_DEPOSIT", "CASH_WITHDRAWAL", "OPENING_LOT", "ORDER_PAID", "ORDER_CONFIRMED", "ORDER_CANCEL_REQUESTED",
               "ORDER_CANCELED", "ORDER_REJECTED", "ORDER_SETTLED", "ORDER_REFUNDED", "DIVIDEND_DECLARED", "DIVIDEND_PAID", "DIVIDEND_REINVESTED", "SPLIT"}


class Workbench(PlanScenariosMixin, ValuationExplanationMixin, PortfolioRiskMixin, ForecastScorecardMixin, MaintenanceMixin, ReconciliationMixin, TReviewMixin, ForecastingMixin, OrderAutomationMixin, ResearchMixin, FundSidebarMixin, SellPreviewMixin, AssetImportMixin, AnalyticsMixin, ImportMixin, BackupMixin, StatementMixin, InitializationMixin, DailyTMixin, MarketDataMixin, StorageMixin):
    def __init__(self, store: Store, root: Path):
        self.store, self.mode, self.root = store, store.mode, root

    def now(self):
        return "2026-10-20T05:30:00+00:00" if self.mode == "demo" else utcnow()

    def today(self):
        return stamp(self.now()).astimezone(SHANGHAI).date().isoformat()

    def state(self, c):
        return project(Store.events(c))

    def fund(self, c, id):
        fund = Store.doc(c, "fund", id)
        require(fund is not None, "FUND_NOT_FOUND", "基金不存在，请先加入自选")
        return fund

    def rules(self, c, fund_id):
        return Store.doc(c, "rule", fund_id, {"verified": False, "_version": 0, "fund_id": fund_id})

    def save_fund(self, p, c=None):
        code = p.get("fund_code") or None
        require(self.mode == "demo" or bool(code and re.fullmatch(r"\d{6}", code)), "INVALID_FUND_CODE", "真实基金代码必须是保留前导零的六位数字")
        require(bool(p.get("name", "").strip()) and len(p["name"]) <= 80, "INVALID_NAME", "请填写 1–80 字符的基金名称")
        share_class, channel = p.get("share_class", "A"), p.get("channel", "手工记录")
        require(p.get("quantity_unit", "FUND_UNIT") == "FUND_UNIT", "UNIT_NOT_SUPPORTED", "首版账本以基金份额核算；克数请作为附件资料保留，不能直接当份额")
        id = p.get("id") or "fund_" + digest([code, share_class, channel])[:16]
        if c is None:
            with self.store.transaction() as connection:
                return self.save_fund(p, connection)
        old = Store.doc(c, "fund", id)
        if old and not p.get("id"):
            return old
        fund = {"id": id, "fund_code": code, "name": p["name"].strip(), "share_class": share_class,
                "channel": channel, "instrument_type": p.get("instrument_type", "OPEN_END_FUND"),
                "quantity_unit": "FUND_UNIT", "currency": "CNY", "watching": p.get("watching", True)}
        return Store.put(c, "fund", id, fund, p.get("expected_version"))

    def save_rules(self, fund_id, p):
        p = dict(p)
        if "purchase_rate_percent" in p:
            p["purchase_rate"] = str(dec(p.pop("purchase_rate_percent")) / 100)
        p["redemption_tiers"] = [dict(t, rate=str(dec(t["rate_percent"]) / 100)) if "rate_percent" in t else t for t in p.get("redemption_tiers", [])]
        with self.store.transaction() as c:
            self.fund(c, fund_id)
            old = self.rules(c, fund_id)
            verified = p.get("verified") is True
            if verified:
                require(bool(p.get("source", "").strip()), "SOURCE_REQUIRED", "核实规则需填写依据或文件来源")
                day(p["effective_from"])
                require(p.get("lot_method") == "FIFO", "RULE_UNSUPPORTED", "首版费用引擎支持已核实的 FIFO、外扣比例费及逐批次取整")
                require(p.get("purchase_method", "EXTERNAL_RATIO") == "EXTERNAL_RATIO", "RULE_UNSUPPORTED", "此费用计算器仅支持已核实的外扣比例申购费")
                require(dec(p["purchase_rate"]) <= 1, "INVALID_RATE", "费率须为小数比例，例如 0.0015")
                dec(p.get("unit_step", "0.01"), positive=True)
                expected_min = 0
                tiers = p.get("redemption_tiers", [])
                require(bool(tiers), "RULES_UNVERIFIED", "请填写完整赎回费率档位")
                for index, tier in enumerate(tiers):
                    require(tier["min_days"] == expected_min and isinstance(tier["min_days"], int), "INVALID_TIERS", "费率档位应从 0 天起连续且不重叠")
                    maximum = tier.get("max_days")
                    require(dec(tier["rate"]) <= 1, "INVALID_RATE", "费率应在 0 到 1 之间")
                    require((maximum is None and index == len(tiers) - 1) or
                            (isinstance(maximum, int) and maximum > expected_min), "INVALID_TIERS", "最后档位应无上限，其他档位需有效上限")
                    expected_min = maximum
                require(tiers[-1].get("max_days") is None, "INVALID_TIERS", "最后一档须覆盖全部剩余持有天数")
            clean = dict(p, fund_id=fund_id, verified=verified, verified_at=self.now() if verified else None)
            clean.pop("expected_version", None)
            saved = Store.put(c, "rule", fund_id, clean, p.get("expected_version", old.get("_version") if old.get("_version") == 0 else None))
            Store.put(c, "rule_history", fund_id + ":" + str(saved["_version"]), saved)
            return saved

    def observe(self, p, c=None, *, provider_evidence=None):
        if c is None:
            with self.store.transaction() as connection:
                return self.observe(p, connection, provider_evidence=provider_evidence)
        self.fund(c, p["fund_id"])
        kind = p["value_kind"]
        require(kind in VALUE_KINDS, "INVALID_VALUE_KIND", "未知行情类型")
        value = dec(p["value"], positive=True)
        date_ = day(p["effective_date"]).isoformat()
        require(date_ <= self.today(), "FUTURE_OBSERVATION", "不能将未来日期写成已经发生的行情")
        source = str(p.get("source", "手工录入")).strip()
        require(0 < len(source) <= 200, "SOURCE_REQUIRED", "请填写数据来源")
        observed = stamp(p.get("observed_at") or self.now()).isoformat()
        published = stamp(p["published_at"]).isoformat() if p.get("published_at") else None
        as_of = stamp(p["as_of"]).isoformat() if p.get("as_of") else None
        require(stamp(observed) <= stamp(self.now()), "FUTURE_OBSERVATION", "观察时间不能晚于当前时间")
        require(not published or stamp(published) <= stamp(observed), "INVALID_TIME", "披露时间不能晚于首次观察时间")
        require(not as_of or stamp(as_of) <= stamp(observed), "INVALID_TIME", "行情本身时点不能晚于本次实际观察时间")
        unit = p.get("unit", "CNY_PER_FUND_UNIT")
        if kind in {"OFFICIAL_NAV", "LOCAL_ESTIMATE", "THIRD_PARTY_ESTIMATE"}:
            require(unit == "CNY_PER_FUND_UNIT", "UNIT_MISMATCH", "基金净值单位必须是人民币/份")
            require(p.get("currency", "CNY") == "CNY", "CURRENCY_MISMATCH", "当前净值核算仅支持人民币，不能把外币报价直接作为人民币净值")
        record = {"fund_id": p["fund_id"], "value_kind": kind, "value": value, "effective_date": date_,
                  "as_of": as_of, "published_at": published, "observed_at": observed,
                  "source": source, "unit": unit, "currency": p.get("currency", "CNY"),
                  "quality": p.get("quality", "MANUAL"), "mode": self.mode}
        if provider_evidence:
            evidence = dict(provider_evidence)
            record["source_receipt_hash"] = evidence.pop("raw_response_hash")
            record["provenance"] = evidence
        # A new response may repeat the same minute; retain that observation's
        # first receipt and first-seen time, rather than duplicating the series.
        raw_hash = digest({k: v for k, v in record.items() if k not in {"observed_at", "source_receipt_hash"}})
        old = c.execute("SELECT id FROM observations WHERE fund_id=? AND value_kind=? AND effective_date=? AND raw_hash=?",
                        (p["fund_id"], kind, date_, raw_hash)).fetchone()
        if old:
            c.execute("UPDATE observations SET fetched_at=? WHERE id=?", (self.now(), old["id"]))
            return {"id": old["id"], "duplicate": True}
        id = "obs_" + digest([self.mode, raw_hash])[:24]
        c.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (id, p["fund_id"], kind, date_, as_of, published,
                  observed, self.now(), source, record["quality"], pack(record), raw_hash))
        self.resolve_predictions(c, p["fund_id"])
        return {"id": id, "duplicate": False}

    def observations(self, c, fund_id, kind=None, latest_per_day=True):
        query = "SELECT * FROM observations WHERE fund_id=?"
        params = [fund_id]
        if kind:
            query += " AND value_kind=?"
            params.append(kind)
        rows = c.execute(query + " ORDER BY effective_date,observed_at,rowid", params).fetchall()
        result = {}
        all_rows = []
        for r in rows:
            item = dict(json.loads(r["payload"]), id=r["id"], fetched_at=r["fetched_at"])
            all_rows.append(item)
            result[(item["value_kind"], item["effective_date"])] = item
        return list(result.values()) if latest_per_day else all_rows

    def snapshot(self, c=None):
        if c is None:
            with self.store.connect() as connection:
                return self.snapshot(connection)
        state = self.state(c)
        funds = Store.docs(c, "fund")
        total_value, total_cost, cost_complete = ZERO, ZERO, True
        missing_nav = []
        def adjusted(quote, fund_id):
            value = dec(quote["value"])
            for split in state["splits"]:
                if split["fund_id"] == fund_id and quote["id"] in split.get("affected_observation_ids", []):
                    value /= dec(split["factor"])
            return value
        for fund in funds:
            history = self.observations(c, fund["id"], "OFFICIAL_NAV")
            latest = history[-1] if history else None
            estimates_by_time = {}
            for x in self.observations(c, fund["id"], latest_per_day=False):
                if x["value_kind"] in {"THIRD_PARTY_ESTIMATE", "LOCAL_ESTIMATE"}:
                    estimates_by_time[(x["value_kind"], x.get("as_of") or x["observed_at"])] = x
            estimates = sorted(estimates_by_time.values(), key=lambda x: (x["effective_date"], x.get("as_of") or x["observed_at"], x["observed_at"]))
            estimate = estimates[-1] if estimates else None
            lots = [l for l in state["lots"].values() if l["fund_id"] == fund["id"] and l["units"]]
            units = sum((l["units"] for l in lots), ZERO)
            reserved = sum((l["reserved"] for l in lots), ZERO)
            sellable = sum((l["units"] - l["reserved"] for l in lots if l["available_from"] and l["available_from"] <= self.today()), ZERO)
            known = all(l["cost"] is not None for l in lots)
            cost = sum((l["cost"] for l in lots), ZERO) if known else None
            valuation_nav = adjusted(latest, fund["id"]) if latest else None
            value = money(units * valuation_nav) if latest else (ZERO if not units else None)
            if value is None:
                missing_nav.append(fund["id"])
            else:
                total_value += value
            if cost is None:
                cost_complete = False
            else:
                total_cost += cost
            previous = history[-2] if len(history) > 1 else None
            change = ((valuation_nav / adjusted(previous, fund["id"]) - 1) if latest and previous else None)
            fund.update(nav=latest, estimate=estimate, estimate_history=[x for x in estimates if x["effective_date"] == estimate["effective_date"]] if estimate else [], change=change, history=history, units=units, reserved_units=reserved,
                        sellable_units=sellable, cost=cost, market_value=value, unrealized=(value - cost if value is not None and cost is not None else None),
                        lots=lots, rules=self.rules(c, fund["id"]), strategy=Store.doc(c, "strategy", fund["id"], {"enabled": False, "_version": 0}),
                        valuation_nav=valuation_nav, valuation_adjusted=bool(latest and valuation_nav != dec(latest["value"])),
                        references=[x for x in self.observations(c, fund["id"]) if x["value_kind"] not in {"OFFICIAL_NAV", "THIRD_PARTY_ESTIMATE", "LOCAL_ESTIMATE"}][-6:],
                        incomplete=any(l["completeness"] != "COMPLETE" for l in lots))
            rule = fund["rules"]
            rule["purchase_rate_percent"] = dec(rule["purchase_rate"]) * 100 if rule.get("purchase_rate") is not None else None
            rule["redemption_tiers"] = [dict(t, rate_percent=dec(t["rate"]) * 100) for t in rule.get("redemption_tiers", [])]
            params = fund["strategy"].get("params", {})
            fund["strategy"]["display_percent"] = {label: dec(params[field]) * 100 if field in params else None for label, field in
                [("target_percent", "target_weight"), ("band_percent", "band"), ("buy_percent", "buy_trigger"), ("sell_percent", "sell_trigger"), ("max_fee_percent", "max_fee_rate")]}
            window = "申请窗口待核实"
            if rule.get("verified"):
                from app.domain.finance import trade_date
                try:
                    target_day = trade_date(self.now(), rule)
                    window = "本日申请窗口内" if target_day == self.today() else "申请计入 " + target_day
                except Problem:
                    window = "开放日期资料待补充"
            fund["states"] = {"window": window, "nav": "当日正式净值已更新" if latest and latest["effective_date"] == self.today() else "当日正式净值待披露",
                              "market": "合成参考市场 · 盘中" if self.mode == "demo" else "参考市场未接入"}
            from app.domain.buckets import BucketBook
            fund['position_buckets']=state['bucket_books'].get(fund['id'],BucketBook()).summary(valuation_nav,self.today())
            fund['position_buckets']['status']='CONFIRMED' if fund['position_buckets']['status']=='READY' else fund['position_buckets']['status']
            fund["statement"] = self.statement_summary(c, fund["id"], history)
            fund["nav_sync"] = Store.doc(c, "nav_sync", fund["id"])
            fund["intraday_sync"] = Store.doc(c, "intraday_sync", fund["id"])
            fund["automatic_market_supported"] = (self.mode == "real" and
                fund.get("fund_code") in SUPPORTED and fund.get("share_class") == "C")
            tracking=state['tracking'].get(fund['id'])
            fund['tracking']=({k:v for k,v in tracking.items() if k not in {'physical_lots','economic_positions','historical_sales'}} if tracking else None)
            if tracking:
                fund['position_buckets']['status']='TRACKING'
                fund['position_buckets']['nav_date']=latest['effective_date'] if latest else None
                groups=fund['position_buckets'].get('groups',{})
                fund['tracking_cost']=sum((g['cost'] for g in groups.values()),ZERO) if groups and all(g['cost'] is not None for g in groups.values()) else None
                fund['tracking_unrealized']=value-fund['tracking_cost'] if value is not None and fund['tracking_cost'] is not None else None
            fund["migration_opening"] = Store.doc(c, "migration_opening", fund["id"])
            fund["reported_holding"] = Store.doc(c, "reported_holding", fund["id"])
            fund["initialization_pending"] = bool(fund["statement"]) and not tracking and not fund["migration_opening"]
            fund["investment_plan_profile"] = Store.doc(c, "investment_plan_profile", fund["id"],
                {"enabled": False, "holding_cap": None, "template": "DAILY_LADDER_V1", "_version": 0})
            fund["daily_t"] = self.daily_t_context(c, fund, estimates, state)
            fund["day_valuation"] = fund_day_valuation(fund, self.today())
            fund['return_valuation'] = fund_return_valuation(fund, state['bucket_books'].get(fund['id'], BucketBook()), self.now())
            fund['holding_composition'] = holding_composition(fund['return_valuation']['summary'])
            fund['reconciliation'] = self.reconciliation_summary(c, fund['id'], state)
            fund['t_review'] = self.t_review_context(c, fund, state)
        equity = None if missing_nav else state["cash"] + total_value + state["pending_buy"] + state["refund_due"] + state["redemption_due"] + state["dividend_due"]
        pnl = equity - state["external_net"] - state["opening_basis"] if equity is not None and state["opening_basis"] is not None else None
        dates = sorted({f["nav"]["effective_date"] for f in funds if f["units"] and f["nav"]})
        pending_initialization = any(f["initialization_pending"] for f in funds)
        snapshot = {"mode": self.mode, "now": self.now(), "business_date": self.today(), "business_timezone": "Asia/Shanghai", "revision": state["revision"],
                "funds": funds, "fund_sidebar": self.sidebar_catalog(c, funds), "portfolio": {"initialization_pending": pending_initialization, 'tracking':bool(state['tracking']),
                "day_valuation": portfolio_day_valuation(funds, self.today()),
                'bucket_values':{k:sum((f['position_buckets'].get('groups',{}).get(k,{}).get('value') or ZERO for f in funds),ZERO) for k in ['BASE','TACTICAL']},
                'historical_redemptions':sum((dec(t['historical_redemptions']) for t in state['tracking'].values()),ZERO),
                "equity": None if pending_initialization else equity, "cash": state["cash"], "available_cash": state["available_cash"],
                "reserved_cash": state["reserved_cash"], "market_value": None if missing_nav or pending_initialization else total_value,
                "pending_buy": state["pending_buy"], "refund_due": state["refund_due"], "redemption_due": state["redemption_due"],
                "dividend_due": state["dividend_due"], "in_transit": state["pending_buy"] + state["refund_due"] + state["redemption_due"] + state["dividend_due"], "pnl": None if pending_initialization else pnl, "realized": state["realized"] if state["realized_complete"] and not pending_initialization else None,
                "unrealized": total_value - total_cost if cost_complete and not missing_nav and not pending_initialization else None,
                "valuation_dates": dates, "missing_nav": missing_nav}, "orders": [dict(o, automation=self.order_progress(c, o, state)[0]) for o in reversed(list(state["orders"].values()))],
                "lots": list(state["lots"].values()), "dividends": list(state["dividends"].values()), "audit": list(reversed(state["audit"]))[:300]}
        snapshot['portfolio']['return_valuation'] = portfolio_return_valuation(funds, snapshot['portfolio'])
        return snapshot

    def event(self, kind, payload, key, occurred_at, expected=None, c=None):
        if c is None:
            with self.store.transaction() as connection:
                return self.event(kind, payload, key, occurred_at, expected, connection)
        require(kind in EVENT_KINDS or kind in {"ORDER_SUBMITTED", "REVERSAL"}, "INVALID_EVENT", "未知事件类型")
        occurred_at = stamp(occurred_at).isoformat()
        require(stamp(occurred_at) <= stamp(self.now()), "FUTURE_EVENT", "实际操作时间不能晚于当前时间")
        if payload.get("fund_id"):
            self.fund(c, payload["fund_id"])
        if kind=='OPENING_LOT':
            from app.domain.buckets import bucket
            payload=dict(payload,bucket=bucket(payload.get('bucket','BASE')))
        if kind == "ORDER_CONFIRMED" and payload.get("confirmation_external_id"):
            key = "confirmation:" + payload["order_id"] + ":" + payload["confirmation_external_id"]
        if kind == "SPLIT":
            prior = c.execute("SELECT payload FROM events WHERE key=?", (key,)).fetchone()
            affected = json.loads(prior[0]).get("affected_observation_ids", []) if prior else [r["id"] for r in c.execute("SELECT id FROM observations WHERE fund_id=?", (payload["fund_id"],))]
            payload = dict(payload, affected_observation_ids=affected)
        duplicate = c.execute("SELECT id FROM events WHERE key=?", (key,)).fetchone()
        state = self.state(c)
        if kind == "OPENING_LOT" and not duplicate:
            require(payload['fund_id'] not in state['tracking'], 'HISTORY_OVERLAP', '已用历史资料建立跟踪起点，不再叠加同一基金期初持仓')
            require(not any(o["fund_id"] == payload["fund_id"] and o["side"] == "BUY" and o["confirmed_units"] > 0 for o in state["orders"].values()),
                    "HISTORY_OVERLAP", "此基金已有确认申购，请勿叠加覆盖相同历史的期初持仓；补正请从原始批次更正入口处理")
        if not duplicate and expected is not None:
            require(expected == state["revision"], "VERSION_CONFLICT", "账本已更新，请刷新后核对再提交")
        result = Store.append(c, kind, payload, key, occurred_at)
        self.state(c)  # Failures roll back the append and all related mutations.
        return dict(result, revision=self.state(c)["revision"])

    def order(self, p, c=None):
        if c is None:
            with self.store.transaction() as connection:
                return self.order(p, connection)
        request_fingerprint = digest({k: v for k, v in p.items() if k not in {"key", "expected_revision"}})
        prior = c.execute("SELECT * FROM events WHERE key=?", (p["key"],)).fetchone()
        if prior:
            prior_payload = json.loads(prior["payload"])
            same = prior_payload.get("_request_fingerprint") == request_fingerprint if prior_payload.get("_request_fingerprint") else all(prior_payload.get(k) == v for k, v in p.items() if k not in {"key", "occurred_at", "expected_revision", "paid_immediately", "allocations"})
            require(same,
                    "IDEMPOTENCY_CONFLICT", "相同幂等键已用于不同申请")
            return {"id": prior["id"], "order_id": prior_payload["id"], "duplicate": True, "revision": self.state(c)["revision"]}
        source_meta = {}
        if p.get("source_recommendation"):
            row = c.execute("SELECT payload FROM plans WHERE id=?", (p["source_recommendation"],)).fetchone()
            require(row is not None, "PLAN_NOT_FOUND", "来源建议不存在")
            plan = json.loads(row[0])
            item = next((i for i in plan["items"] if i["fund_id"] == p["fund_id"]), None)
            require(item is not None, "PLAN_FUND_MISMATCH", "来源建议不包含此基金")
            source_meta = {"source_plan_stale": stamp(plan["expires_at"]) <= stamp(self.now()) or plan["ledger_version"] != self.state(c)["revision"],
                           "strategy_version": item["config_version"],
                           "strategy_id": p["fund_id"] if item["action"] == p["side"] and item["action"] in {"BUY", "SELL"} else None}
        payload = {k: v for k, v in p.items() if k not in {"key", "occurred_at", "expected_revision", "paid_immediately"}}
        payload.update(source_meta)
        from app.domain.buckets import bucket
        payload['bucket']=bucket(payload.get('bucket','BASE'))
        # New real records describe trades outside the workbench cash book.
        # Old events without this field retain their original tracked-cash replay.
        payload['cash_source']=payload.get('cash_source', 'EXTERNAL' if self.mode=='real' else 'TRACKED')
        require("auto_confirm" not in payload or isinstance(payload["auto_confirm"], bool), "INVALID_STATE", "自动入账选项须为布尔值")
        payload["_request_fingerprint"] = request_fingerprint
        if payload.get("strategy_id") and "strategy_version" not in payload:
            payload["strategy_version"] = Store.doc(c, "strategy", payload["fund_id"], {"_version": 0})["_version"]
        payload["id"] = payload.get("id") or "ord_" + digest(p["key"])[:20]
        if payload["side"] == "SELL" and not payload.get("allocations"):
            rule = self.rules(c, payload["fund_id"])
            require(rule.get("verified") and rule.get("lot_method") == "FIFO", "ALLOCATION_REQUIRED", "渠道批次规则未核实，请逐批填写本次实际申请占用份额")
            left, allocations = dec(payload["requested"], positive=True), []
            state = self.state(c)
            for lot in sorted(state["lots"].values(), key=lambda l: (l["acquired"] or "9999", l["id"])):
                if lot["fund_id"] != payload["fund_id"] or (lot["available_from"] and lot["available_from"] > self.today()):
                    continue
                take = min(left, lot["units"] - lot["reserved"])
                if take > 0:
                    allocations.append({"lot_id": lot["id"], "units": str(take)})
                    left -= take
                if not left:
                    break
            require(left == 0, "UNITS_RESERVED", "可赎回份额不足")
            payload["allocations"] = allocations
        occurred_at = submission_time(p["occurred_at"], payload.get("time_precision"))
        if payload.get("time_precision") == "BEFORE_CUTOFF":
            payload["submission_basis"] = "USER_STATED_BEFORE_1500"
        result = self.event("ORDER_SUBMITTED", payload, p["key"], occurred_at, p.get("expected_revision"), c)
        if p.get("paid_immediately") and p["side"] == "BUY":
            payment = {"order_id": payload["id"], "amount": payload["requested"]}
            if payload.get("time_precision") == "BEFORE_CUTOFF":
                payment.update(time_precision="DAY", submission_basis="USER_STATED_BEFORE_1500")
            self.event("ORDER_PAID", payment, p["key"] + ":paid", occurred_at, c=c)
        return dict(result, order_id=payload["id"], revision=self.state(c)["revision"])

    def preview_order(self, p):
        with self.store.connect() as c:
            state = self.state(c)
            rule = self.rules(c, p["fund_id"])
            nav = p["nav"]
            if p["side"] == "BUY":
                return purchase_preview(p["requested"], nav, rule)
            lots = [l for l in state["lots"].values() if l["fund_id"] == p["fund_id"]]
            if p.get("net_target"):
                return solve_net_target(lots, p["net_target"], nav, rule, p.get("trade_date") or self.today())
            return redemption_preview(lots, p["requested"], nav, rule, p.get("trade_date") or self.today())

    def correct(self, p):
        with self.store.transaction() as c:
            require(stamp(p["occurred_at"]) <= stamp(self.now()), "FUTURE_EVENT", "更正记录时间不能在未来")
            state = self.state(c)
            duplicate = c.execute("SELECT id FROM events WHERE key=?", (p["key"] + ":reverse",)).fetchone()
            require(bool(duplicate) or p.get("expected_revision") == state["revision"], "VERSION_CONFLICT", "账本已更新，请刷新后重试更正")
            first = Store.append(c, "REVERSAL", {"event_id": p["event_id"], "reason": p["reason"]}, p["key"] + ":reverse", stamp(p["occurred_at"]).isoformat())
            if p.get("replacement"):
                replacement = p["replacement"]
                require(stamp(replacement["occurred_at"]) <= stamp(self.now()), "FUTURE_EVENT", "替代记录时间不能在未来")
                require(replacement["kind"] not in {"REVERSAL"}, "INVALID_REVERSAL", "不能用冲正事件替代业务事件")
                payload = dict(replacement["payload"])
                original = next((e for e in state["audit"] if e["id"] == p["event_id"]), None)
                if original and original["payload"].get("confirmation_basis") == AUTO_BASIS:
                    # An explicit correction wins permanently over the automatic reconstruction.
                    for field in ["auto_policy_version", "nav_observation_id", "rule_version", "calendar_basis", "auto_assumption"]:
                        payload.pop(field, None)
                    payload.update(confirmation_basis="CHANNEL_RECORD", corrected_from_auto=p["event_id"])
                Store.append(c, replacement["kind"], dict(payload, _replaces=p["event_id"]), p["key"] + ":replace", stamp(replacement["occurred_at"]).isoformat())
            self.state(c)
            return first

    def resolve_predictions(self, c, fund_id):
        # Bound later by the prediction module; observation ingestion is always independent of a model.
        for row in c.execute("SELECT * FROM predictions WHERE id NOT IN (SELECT prediction_id FROM resolutions)").fetchall():
            p = json.loads(row["payload"])
            if p["fund_id"] != fund_id:
                continue
            candidates = [x for x in self.observations(c, fund_id, "OFFICIAL_NAV") if x["effective_date"] == p["target_date"]
                          and stamp(x["observed_at"]) >= stamp(p["generated_at"])]
            if candidates:
                actual = candidates[-1]
                error = dec(actual["value"]) - dec(p["predicted_nav"])
                c.execute("INSERT OR IGNORE INTO resolutions VALUES(?,?)", (p["id"], pack({"actual": actual, "error": error,
                    "absolute_percentage_error": abs(error) / dec(actual["value"]), "resolved_at": self.now()})))
