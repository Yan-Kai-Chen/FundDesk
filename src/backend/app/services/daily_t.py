import json

from app.domain.finance import ZERO, amount, dec, require
from app.domain.daily_t import PRESETS, daily_context, reference
from app.persistence.store import Store
from app.providers.public_nav import MARKET_SCOPES, SUPPORTED


class DailyTMixin:
    def save_investment_plan(self, fund_id, p):
        fields = {"enabled", "holding_cap", "template", "expected_version"}
        require(set(p) == fields, "INVALID_INVESTMENT_PLAN", "投资计划只需启用状态、持仓上限、固定模板和当前版本")
        require(type(p["enabled"]) is bool, "INVALID_INVESTMENT_PLAN", "计划启用状态须为布尔值")
        require(isinstance(p["template"], str) and p["template"] in {"DAILY_LADDER_V1", "DRAWDOWN_GUARD_V2"},
                "INVALID_INVESTMENT_PLAN", "请选择每日分档或回撤防御计划")
        require(type(p["expected_version"]) is int and p["expected_version"] >= 0,
                "VERSION_REQUIRED", "保存投资计划需要当前版本，请刷新后重试")
        cap = amount(p["holding_cap"], positive=True)
        require(cap <= dec("100000000"), "INVALID_INVESTMENT_PLAN", "计划持仓上限不能超过一亿元")
        clean = {"fund_id": fund_id, "enabled": p["enabled"], "holding_cap": format(cap, ".2f"),
                 "template": p["template"]}
        with self.store.transaction() as c:
            product = self.fund(c, fund_id)
            require(self.mode != "real" or (product.get("fund_code") in SUPPORTED and product.get("share_class") == "C"),
                    "PLAN_PRODUCT_UNSUPPORTED", "该份额类别尚未接入每日投资计划")
            old = Store.doc(c, "investment_plan_profile", fund_id, {"_version": 0})
            require(p["expected_version"] == old["_version"], "VERSION_CONFLICT", "记录已被其他操作修改，请刷新后重试")
            if all(old.get(k) == v for k, v in clean.items()):
                return old
            if p["enabled"]:
                snapshot = self.snapshot(c)
                fund = next(f for f in snapshot["funds"] if f["id"] == fund_id)
                summary = fund.get("position_buckets") or {}
                groups = summary.get("groups") or {}
                require(not fund.get("initialization_pending") and dec(fund["units"]) > ZERO and
                        summary.get("status") in {"READY", "CONFIRMED", "TRACKING"} and
                        summary.get("total_pnl") is not None and
                        set(groups) == {"BASE", "TACTICAL"} and
                        all(g.get("cost") is not None and g.get("value") is not None for g in groups.values()) and
                        dec(summary.get("total_units", "0")) == dec(fund["units"]),
                        "HOLDINGS_UNKNOWN", "请先完成可核对的持仓及底仓、T 仓记录，再启用投资计划")
            saved = Store.put(c, "investment_plan_profile", fund_id, dict(clean, updated_at=self.now()), p["expected_version"])
            Store.put(c, "investment_plan_profile_history", f"{fund_id}:{saved['_version']}",
                      dict(saved, profile_version=saved["_version"]))
            history = [(row["id"], json.loads(row["payload"])) for row in c.execute(
                "SELECT id,payload FROM documents WHERE kind='investment_plan_profile_history'")]
            own = sorted((row for row in history if row[1].get("fund_id") == fund_id),
                         key=lambda row: row[1]["profile_version"], reverse=True)
            for id, _ in own[20:]:
                c.execute("DELETE FROM documents WHERE kind='investment_plan_profile_history' AND id=?", (id,))
            return saved

    def save_t_profile(self, fund_id, p):
        require(set(p) <= {"level", "expected_version"}, "INVALID_T_PROFILE", "做 T 参数只需选择预设档位")
        require(isinstance(p.get("level"), str) and p["level"] in {x["level"] for x in PRESETS},
                "INVALID_T_PROFILE", "请选择观察、谨慎、均衡或积极")
        require(isinstance(p.get("expected_version"), int) and not isinstance(p.get("expected_version"), bool),
                "VERSION_REQUIRED", "保存档位需要当前版本，请刷新后重试")
        with self.store.transaction() as c:
            self.fund(c, fund_id)
            payload = {"fund_id": fund_id, "level": p["level"], "updated_at": self.now()}
            saved = Store.put(c, "t_profile", fund_id, payload, p["expected_version"])
            Store.put(c, "t_profile_history", f"{fund_id}:{saved['_version']}", saved)
            return saved

    def daily_t_context(self, c, fund, estimates, state):
        profile = Store.doc(c, "t_profile", fund["id"], {"level": "OBSERVE", "_version": 0})
        scope = (MARKET_SCOPES.get(fund.get("fund_code"), "UNKNOWN")
                 if fund.get("share_class") == "C" else "UNKNOWN") if self.mode == "real" else "CN"
        corporate = bool((fund.get("nav_sync") or {}).get("corporate_actions")) or (fund.get("nav_sync") or {}).get("corporate_actions_complete") is False or any(
            e["payload"].get("fund_id") == fund["id"] and not e["reversed"] and
            e["kind"] in {"SPLIT", "DIVIDEND_DECLARED"} for e in state["audit"])
        return daily_context(fund, profile, estimates, self.now(), scope, corporate)

    @staticmethod
    def daily_t_references(snapshot):
        from app.domain.investment_plan import investment_plan_reference
        return [(investment_plan_reference(f, snapshot["orders"], snapshot["now"])
                 if f.get("investment_plan_profile", {}).get("enabled") else
                 reference(f, snapshot["orders"], snapshot["now"])) for f in snapshot["funds"]]
