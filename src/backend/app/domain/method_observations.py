"""Budget-free observations. Method targets/amounts still need explicit evidence."""
from decimal import Decimal, localcontext

from app.domain.finance import ZERO, SHANGHAI, dec, stamp
from app.domain.research_calendar import reference_sessions, SOURCES
from app.providers.research_history import PRODUCTS
from app.research_methods.engine.models import generate
from app.research_methods.engine.risk import project as constrain_targets
from app.research_methods.engine.domain import visible_rows
from app.research_methods.engine.domain import positive as research_positive, dec as research_decimal

LABELS = {"TREND": "趋势跟随", "VALUE": "估值布局", "REBALANCE": "仓位再平衡"}


def visible_series(data, as_of):
    if not data or stamp(data["observed_at"]) > stamp(as_of):
        return {}
    local = stamp(as_of).astimezone(SHANGHAI)
    return {r[0]: research_positive(r[-1]) for r in data["rows"]
            if r[0] < local.date().isoformat() or r[0] == local.date().isoformat() and local.hour >= 15}


def descriptive_stats(values):
    if len(values) < 2:
        return {"return_20": None, "return_60": None, "volatility_60": None, "drawdown_252": None}
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    recent = returns[-60:]
    mean = sum(recent, ZERO) / len(recent)
    volatility = ((sum(((r - mean) ** 2 for r in recent), ZERO) / (len(recent) - 1)) * 252).sqrt() if len(recent) == 60 else None
    # Keep shorter histories usable without a fictitious 252-session statistic.
    peak, drawdown = values[-253] if len(values) >= 253 else values[0], ZERO
    for value in values[-253:]:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return {"return_20": values[-1] / values[-21] - 1 if len(values) >= 21 else None,
            "return_60": values[-1] / values[-61] - 1 if len(values) >= 61 else None,
            "volatility_60": volatility, "drawdown_252": drawdown if len(values) >= 253 else None}


def observe_methods(snapshot, datasets, policy, methods):
    with localcontext() as context:
        context.prec = 36
        return _observe(snapshot, datasets, policy, methods)


def _observe(snapshot, datasets, policy, methods):
    as_of = snapshot["now"]
    today = snapshot["business_date"]
    sessions = reference_sessions()
    completed = [d for d in sessions if d < today or d == today and stamp(as_of).astimezone(SHANGHAI).hour >= 15]
    decision = min((f["nav"]["effective_date"] for f in snapshot["funds"] if f.get("nav")), default=today)
    prior_month = [d for d in completed if d[:7] < decision[:7]] if decision in sessions else []
    anchor = prior_month[-1] if prior_month else None
    end = sessions.index(anchor) if anchor else 0
    window = sessions[end - 252:end + 1] if end >= 252 else []
    cash = visible_series(datasets.get("cash"), as_of)
    all_series = {f["id"]: visible_series(datasets.get(f.get("fund_code")) if f.get("share_class") == "C" else None, as_of) for f in snapshot["funds"]}
    equity = None if any(f["market_value"] is None for f in snapshot["funds"]) else sum((dec(f["market_value"]) for f in snapshot["funds"]), ZERO)
    budget = dec(policy["cash_budget"]) if policy.get("cash_budget") is not None else ZERO
    denominator = equity + budget if equity is not None else None
    rows, candidates, trend_missing = [], [], []
    for f in snapshot["funds"]:
        kind, scope, exposure = PRODUCTS.get(f.get("fund_code"), ("UNKNOWN", "UNKNOWN", "UNKNOWN"))
        if f.get("share_class") != "C": kind, scope = "UNKNOWN", "UNKNOWN"
        series = all_series[f["id"]]
        dates = sorted(series)
        stats = descriptive_stats([series[d] for d in dates]) if dates else descriptive_stats([])
        weight = dec(f["market_value"]) / denominator if denominator and f["market_value"] is not None else None
        base = {"fund_id": f["id"], "fund_code": f.get("fund_code"), "kind": kind, "market_scope": scope,
                "exposure": exposure, "weight": weight, "stats": stats, "history_count": len(dates),
                "history_start": dates[0] if dates else None, "history_end": dates[-1] if dates else None,
                "trend": {"status": "NOT_APPLICABLE", "reasons": ["MARKET_MAPPING_UNSUPPORTED" if scope != "CN_DOMESTIC" else "PRODUCT_KIND_NOT_APPLICABLE"]},
                "value": {"status": "NOT_APPLICABLE", "reasons": ["MARKET_MAPPING_UNSUPPORTED" if scope != "CN_DOMESTIC" else "PRODUCT_KIND_NOT_APPLICABLE"]},
                "rebalance": {"status": "NOT_APPLICABLE", "reasons": ["MARKET_MAPPING_UNSUPPORTED"]}}
        if scope == "CN_DOMESTIC" and kind in {"PASSIVE_EQUITY", "PASSIVE_GOLD"}:
            missing = (["TREND_CALENDAR_HISTORY_SHORT"] if not window else [])
            if any(d not in series for d in window): missing.append("TREND_HISTORY_INCOMPLETE")
            if any(d not in cash for d in window): missing.append("CASH_BENCHMARK_INCOMPLETE")
            trend = {"status": "WAIT" if missing else "OBSERVATION_READY", "reasons": missing,
                     "anchor": anchor, "lookback_sessions": 252, "selected": None}
            if missing:
                trend_missing.append(f["id"])
            else:
                total = series[window[-1]] / series[window[0]] - 1
                benchmark = cash[window[-1]] / cash[window[0]] - 1
                trend.update(total_return=total, cash_return=benchmark, excess_vs_cash=total - benchmark,
                             qualifies=total - benchmark > Decimal("0.01"))
                if trend["qualifies"]: candidates.append((total, f["id"]))
            base["trend"] = trend
        if scope == "CN_DOMESTIC" and kind == "PASSIVE_EQUITY":
            base["value"] = {"status": "WAIT", "reasons": ["VALUATION_MONTH_HISTORY_INCOMPLETE"],
                             "required_months": 60, "available_months": 0, "anchor": anchor}
        if scope == "CN_DOMESTIC" and kind != "UNKNOWN":
            target = policy.get("target_weights", {}).get(f["id"])
            base["rebalance"] = {"status": "OBSERVATION_READY" if target is not None and weight is not None else "WAIT",
                                 "reasons": [] if target is not None and weight is not None else ["TARGET_WEIGHTS_REQUIRED"],
                                 "current_weight": weight, "target_weight": target,
                                 "gap": research_decimal(target) - weight if target is not None and weight is not None else None}
        rows.append(base)
    counts = {r["exposure"]: sum(x["exposure"] == r["exposure"] and x["trend"]["status"] != "NOT_APPLICABLE" for x in rows) for r in rows}
    for row in rows:
        if row["trend"]["status"] != "NOT_APPLICABLE" and counts[row["exposure"]] > 1:
            row["trend"]["status"] = "WAIT"
            row["trend"]["reasons"].append("DUPLICATE_EXPOSURE_REQUIRES_MAPPING")
            trend_missing.append(row["fund_id"])
    winners = [id for _, id in sorted(candidates, key=lambda pair: (-pair[0], pair[1]))[:2]] if not trend_missing else []
    for row in rows:
        if row["trend"]["status"] == "OBSERVATION_READY":
            row["trend"]["selected"] = row["fund_id"] in winners if not trend_missing else None
            if trend_missing: row["trend"]["reasons"].append("PEER_HISTORY_INCOMPLETE_RANKING_PAUSED")
    output = {}
    for method in methods:
        key = {"TREND": "trend", "VALUE": "value", "REBALANCE": "rebalance"}[method]
        eligible = [r for r in rows if r[key]["status"] != "NOT_APPLICABLE"]
        ready = sum(r[key]["status"] == "OBSERVATION_READY" for r in eligible)
        output[method] = {"method": method, "label": LABELS[method], "observation_ready": ready,
                          "applicable_count": len(eligible), "status": "NOT_APPLICABLE" if not eligible else "OBSERVATION_READY" if ready == len(eligible) else "WAIT",
                          "items": [{"fund_id": r["fund_id"], **r[key]} for r in rows]}
    risk = portfolio_risk(snapshot["funds"], all_series, sessions, completed, denominator)
    return {"methods": output, "funds": rows, "trend_anchor": anchor,
            "portfolio_risk": risk, "equity_reference": denominator,
            "scope": "REGISTERED_FUNDS_PLUS_EXPLICIT_RESEARCH_BUDGET", "calendar_sources": SOURCES,
            "notice": "观察特征不等于可执行金额；正式三方法方案需完整的目标、风险、日历、批次和费用证据。"}


def portfolio_risk(funds, series, sessions, completed, denominator):
    active = [f for f in funds if dec(f["units"]) > 0]
    if not active or not denominator or not completed:
        return {"status": "WAIT", "annualized_volatility": None}
    cutoff = min((f["nav"]["effective_date"] for f in active if f.get("nav")), default=None)
    if len({f["nav"]["effective_date"] if f.get("nav") else None for f in active}) != 1:
        return {"status": "WAIT", "annualized_volatility": None}
    if cutoff not in sessions:
        return {"status": "WAIT", "annualized_volatility": None}
    end = sessions.index(cutoff)
    dates = sessions[max(0, end - 60):end + 1]
    if len(dates) < 61 or any(d not in series[f["id"]] for f in active for d in dates):
        return {"status": "WAIT", "annualized_volatility": None}
    returns = {}
    weights = {f["id"]: dec(f["market_value"]) / denominator for f in active if f["market_value"] is not None}
    if len(weights) != len(active):
        return {"status": "WAIT", "annualized_volatility": None}
    for f in active:
        v = [series[f["id"]][d] for d in dates]
        returns[f["id"]] = [b / a - 1 for a, b in zip(v, v[1:])]
    weighted = [sum((weights[id] * returns[id][i] for id in weights), ZERO) for i in range(60)]
    mean = sum(weighted, ZERO) / 60
    vol = (sum(((r - mean) ** 2 for r in weighted), ZERO) / 59 * 252).sqrt()
    return {"status": "READY", "annualized_volatility": vol, "sessions": 60, "date": cutoff,
            "maximum_weight": max(weights.values()), "scope": "当前登记基金配比的历史协方差估计"}


def method_targets(snapshot, datasets, policy, methods, config, level, imported=None):
    """Reuse frozen generators/risk math without inventing an execution calendar or cash.

    Unit upper bounds are mathematical no-leverage bounds for the declared fund
    scope, not an asserted account allocation preference. No fee/amount layer runs.
    """
    if policy["level"] == "OBSERVE":
        return None
    funds = snapshot["funds"]
    if not funds or set(policy["target_weights"]) != {f["id"] for f in funds}:
        return None
    dates = {f["nav"]["effective_date"] if f.get("nav") else None for f in funds}
    if len(dates) != 1 or None in dates:
        return None
    decision = next(iter(dates))
    known_exposures = [PRODUCTS[f["fund_code"]][2] for f in funds if f.get("fund_code") in PRODUCTS and f.get("share_class") == "C"]
    if len(known_exposures) != len(set(known_exposures)):
        return {"decision_session": decision, "methods": {m: {"method": m, "label": LABELS[m], "analysis_status": "WAIT",
            "reasons": ["DUPLICATE_EXPOSURE_REQUIRES_MAPPING"], "items": [], "risk": None} for m in methods},
            "execution_enabled": False, "price_basis": "REGISTERED_FUND_SCOPE_TARGETS_ONLY", "intraday_scenario": {"status": "NOT_REQUESTED"}}
    sessions = imported["calendar"]["sessions"] if imported else reference_sessions()
    earlier = [d for d in sessions if d <= decision and d[:7] < decision[:7]]
    if decision not in sessions or not earlier:
        return None
    equity = sum((dec(f["market_value"]) for f in funds if f["market_value"] is not None), ZERO)
    if any(f["market_value"] is None for f in funds) or equity <= 0:
        return None
    equity += dec(policy["cash_budget"]) if policy.get("cash_budget") is not None else ZERO
    data = {"_equity": equity, "_anchor": earlier[-1], "_dealing_date": decision,
            "decision_session": decision, "calendar": {"sessions": sessions},
            "cash_benchmark": {"tr_series": [{"date": d, "value": str(v)} for d, v in visible_series(datasets.get("cash"), snapshot["now"]).items()]},
            "instruments": [], "portfolio": {"holdings": []},
            "policy": {"target_weights": policy["target_weights"], "max_invested_weight": "1", "max_asset_weight": "1",
                       "max_group_weights": {}, "cash_reserve": "0", "volatility_limit": level["volatility_limit"]}}
    imported_assets = {a["id"]: a for a in imported["instruments"]} if imported else {}
    imported_holdings = {h["asset_id"]: h for h in imported["portfolio"]["holdings"]} if imported else {}
    for f in funds:
        kind, scope, exposure = PRODUCTS.get(f.get("fund_code"), ("UNKNOWN", "UNKNOWN", "UNKNOWN"))
        if f.get("share_class") != "C": kind, scope = "UNKNOWN", "UNKNOWN"
        series = visible_series(datasets.get(f.get("fund_code")) if f.get("share_class") == "C" else None, snapshot["now"])
        base = f["position_buckets"].get("groups", {}).get("BASE", {}).get("units")
        if base is None: return None
        if f["id"] in imported_holdings:
            base = max(dec(base), dec(imported_holdings[f["id"]]["protected_units"]))
        valuations = imported_assets.get(f["id"], {}).get("valuation_series", [])
        valuations = visible_rows(valuations, stamp(snapshot["now"]), datetime_date(earlier[-1]), True)
        data["instruments"].append({"id": f["id"], "kind": kind, "market_scope": scope,
            "eligible_from": min(series, default=decision), "eligible_to": None, "risk_group": exposure,
            "tr_series": [{"date": d, "value": str(v)} for d, v in series.items()], "valuation_series": valuations})
        data["policy"]["max_group_weights"][exposure] = "1"
        data["portfolio"]["holdings"].append({"asset_id": f["id"], "protected_units": str(base),
            "mark": {"value": str(f["nav"]["value"])}, "_value": dec(f["market_value"])})
    output = {"decision_session": decision, "methods": {}, "execution_enabled": False,
              "price_basis": "REGISTERED_FUND_SCOPE_TARGETS_ONLY", "intraday_scenario": {"status": "NOT_REQUESTED"},
              "notice": "仅比较基金范围内的目标配比与历史风险，不声明账户现金或申赎开放，不生成交易金额。"}
    with localcontext() as context:
        context.prec = 36
        for method in methods:
            model = generate(method, data, config)
            result = {"method": method, "label": LABELS[method], "analysis_status": model["status"],
                      "reasons": model["reasons"], "features": model["details"], "items": [], "risk": None}
            if model["raw_weights"] is not None:
                weights, risk = constrain_targets(model["raw_weights"], model["locked"], data, config["risk"])
                result["risk"] = risk
                if weights is None:
                    result.update(analysis_status=risk["status"], reasons=risk["reasons"])
                else:
                    for f in funds:
                        current, target = dec(f["market_value"]) / equity, weights[f["id"]]
                        band = dec(level["band"])
                        lower, upper = max(ZERO, target - band), min(Decimal("1"), target + band)
                        direction = "INCREASE" if current < lower else "DECREASE" if current > upper else "HOLD"
                        applicable = model["details"][f["id"]]["applicability"] == "APPLICABLE"
                        result["items"].append({"asset_id": f["id"], "current_weight": current,
                            "target_weight": target, "target_band": [lower, upper],
                            "signal_action": direction if applicable else None,
                            "execution_status": "TARGET_ONLY" if applicable else "NOT_APPLICABLE", "gross_reference": None,
                            "fee_reference": None, "units_reference": None, "reasons": ["TARGET_COMPARISON_ONLY"] if applicable else model["details"][f["id"]]["reasons"]})
            output["methods"][method] = result
    return output


def datetime_date(value):
    from datetime import date
    return date.fromisoformat(value)
