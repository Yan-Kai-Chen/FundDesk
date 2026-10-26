"""Deterministic public API. The caller owns storage and manual invocation."""
import copy
from datetime import time
from decimal import localcontext
from . import VERSION
from .domain import (InputError, prepare, digest, serial, require, dec, stamp, CN, ZERO, ONE)
from .models import LABELS, generate
from .risk import project
from .execution import reference_plans


def validate_config(config):
    require(isinstance(config, dict), "Method configuration must be an object")
    require(set(config) == {"version", "status", "trend", "value", "risk"},
            "Unknown or missing method configuration section")
    shapes = {
        "trend": {"lookback_sessions", "top_k", "minimum_excess_return"},
        "value": {"minimum_months", "maximum_months", "minimum_metrics",
                  "percentile_edges", "multipliers"},
        "risk": {"lookback_returns", "annualization_sessions"}}
    for section, fields in shapes.items():
        require(isinstance(config[section], dict) and set(config[section]) == fields,
                "Unknown or missing method parameter in " + section)
    require(isinstance(config.get("version"), str), "Method configuration version required")
    require(config["status"] == "RESEARCH_CANDIDATE_NOT_OPTIMIZED", "Invalid method status")
    for value in (config["trend"]["lookback_sessions"], config["trend"]["top_k"],
                  config["value"]["minimum_months"], config["value"]["maximum_months"],
                  config["value"]["minimum_metrics"], config["risk"]["lookback_returns"],
                  config["risk"]["annualization_sessions"]):
        require(type(value) is int and value > 0, "Invalid integer method parameter")
    require(config["risk"]["lookback_returns"] >= 2, "Covariance requires at least two returns")
    require(config["value"]["minimum_months"] <= config["value"]["maximum_months"],
            "Invalid valuation history bounds")
    require(config["value"]["minimum_metrics"] <= 3, "Too many required valuation metrics")
    dec(config["trend"]["minimum_excess_return"], ZERO, ONE)
    edges = [dec(v, ZERO, ONE) for v in config["value"]["percentile_edges"]]
    require(edges == sorted(set(edges)), "Percentile edges must be strictly ascending")
    require(len(config["value"]["multipliers"]) == len(edges) + 1, "Value bucket count mismatch")
    for value in config["value"]["multipliers"]:
        dec(value, ZERO)


def method_results(data, config, methods, snapshot_hash, config_hash, scenario=False):
    results = {}
    for method in methods:
        generated = generate(method, data, config)
        result = {"method": method, "label": LABELS[method],
                      "analysis_status": generated["status"],
                      "reasons": generated["reasons"], "features": generated["details"],
                      "raw_weights": generated["raw_weights"], "target_weights": None,
                      "risk": None, "items": [], "budget": None,
                      "signal_anchor": data["_anchor"] if method != "REBALANCE" else data["decision_session"],
                  "analysis_id": digest({"snapshot": snapshot_hash, "config": config_hash,
                                         "method": method, "intraday_scenario": scenario})}
        if generated["raw_weights"] is not None:
            target, risk = project(generated["raw_weights"], generated["locked"],
                                   data, config["risk"])
            result["risk"] = risk
            if target is None:
                result.update(analysis_status=risk["status"], reasons=risk["reasons"])
            else:
                result["target_weights"] = target
                result.update(reference_plans(target, generated["details"], data))
                if scenario:
                    for item in result["items"]:
                        item["price_basis"] = "SAME_DAY_ESTIMATE_SCENARIO_NOT_FILL"
                        item["reference_nav_date"] = data["_as_of"].astimezone(CN).date().isoformat()
        results[method] = result
    return results


def intraday_scenario(data, config, methods, snapshot_hash, config_hash):
    result = {"status": "NOT_REQUESTED", "reasons": [], "quotes": {}, "methods": {},
              "portfolio_equity_reference": None}
    if not data.get("intraday_scenario_requested", False):
        return result
    result["status"] = "WAIT"
    local = data["_as_of"].astimezone(CN)
    today = local.date().isoformat()
    sessions = data["calendar"]["sessions"]
    if today not in sessions or local.time() < time(9, 30) or local.time() >= time(15):
        result["reasons"] = ["SCENARIO_OUTSIDE_CN_DEALING_SESSION"]
        return result
    previous = [d for d in sessions if d < today]
    if not previous or data["decision_session"] != previous[-1]:
        result["reasons"] = ["SCENARIO_PREVIOUS_NAV_SESSION_REQUIRED"]
        return result
    holdings = {h["asset_id"]: h for h in data["portfolio"]["holdings"]}
    for asset in data["instruments"]:
        key, quote = asset["id"], asset["intraday_reference"]
        if quote is None:
            result["reasons"].append(key + ":SAME_DAY_QUOTE_MISSING")
            continue
        when = stamp(quote["as_of"])
        age = (data["_as_of"] - when).total_seconds()
        if quote["date"] != today or when.astimezone(CN).date().isoformat() != today or age > 900:
            result["reasons"].append(key + ":SAME_DAY_QUOTE_STALE")
        mark = holdings[key]["mark"]
        if quote["baseline_nav_date"] != mark["date"] or dec(quote["baseline_nav"]) != dec(mark["value"]):
            result["reasons"].append(key + ":QUOTE_BASELINE_MISMATCH")
        result["quotes"][key] = copy.deepcopy(quote)
    if result["reasons"]:
        return result
    scenario = copy.deepcopy(data)
    quotes = result["quotes"]
    equity = dec(scenario["portfolio"]["cash_settled"])
    for holding in scenario["portfolio"]["holdings"]:
        quote = quotes[holding["asset_id"]]
        holding["mark"]["value"] = quote["value"]
        holding["_value"] = holding["_units"] * dec(quote["value"])
        equity += holding["_value"]
    scenario["_equity"] = equity
    result.update(status="READY", portfolio_equity_reference=equity,
                  methods=method_results(scenario, config, methods, snapshot_hash, config_hash, True))
    return result


def evaluate(raw, config, methods=None):
    from .contracts import validate_input, validate_output
    validate_input(raw)
    validate_config(config)
    methods = list(LABELS if methods is None else methods)
    require(methods and len(set(methods)) == len(methods) and set(methods) <= set(LABELS),
            "Unknown or duplicate method")
    with localcontext() as context:
        context.prec = 36
        data = prepare(raw)
        basis = copy.deepcopy({key: value for key, value in data.items() if not key.startswith("_")})
        for holding in basis["portfolio"]["holdings"]:
            for key in list(holding):
                if key.startswith("_"):
                    del holding[key]
        snapshot_hash = digest(basis)
        config_hash = digest(config)
        results = method_results(data, config, methods, snapshot_hash, config_hash)
        scenario = intraday_scenario(data, config, methods, snapshot_hash, config_hash)
        disagreement = {}
        for asset in data["instruments"]:
            directions = {}
            for method, result in results.items():
                item = next((x for x in result["items"] if x["asset_id"] == asset["id"]), None)
                directions[method] = item["signal_action"] if item else None
            disagreement[asset["id"]] = directions
        output = serial({
            "schema_version": "1.0", "engine_version": VERSION,
            "mode": data["mode"], "evidence_status": (
                "SYNTHETIC_FUNCTIONAL_ONLY" if data["mode"] == "SYNTHETIC"
                else "UNVALIDATED_RESEARCH"),
            "execution_enabled": False,
            "as_of": raw["as_of"], "decision_session": data["decision_session"],
            "snapshot_id": raw["snapshot_id"], "snapshot_hash": snapshot_hash,
            "method_config_hash": config_hash, "method_config_version": config["version"],
            "valid_until": data["_valid_until"], "dealing_date_if_applied": data["_dealing_date"],
            "portfolio_equity_reference": data["_equity"], "methods": results,
            "intraday_scenario": scenario,
            "comparison": {"same_snapshot": True, "same_constraints": True,
                           "amounts_are_not_additive": True,
                           "probability_of_rising": None,
                           "directions_by_asset": disagreement,
                           "horizons_are_different": True}
        })
        validate_output(output)
        return output
