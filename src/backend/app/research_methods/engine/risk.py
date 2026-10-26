"""Shared caps and sample-covariance risk constraint, without leverage."""
from .domain import ZERO, ONE, dec, exact_values


class RiskBlocked(ValueError):
    pass


def covariance(data, ids, config):
    n = config["lookback_returns"]
    sessions = data["calendar"]["sessions"]
    end = sessions.index(data["decision_session"])
    if end < n:
        return None, ["RISK_CALENDAR_HISTORY_SHORT"]
    dates = sessions[end - n:end + 1]
    assets = {a["id"]: a for a in data["instruments"]}
    returns, reasons = {}, []
    for key in ids:
        values, missing = exact_values(assets[key], dates)
        reasons.extend([key + ":" + x for x in missing])
        if values:
            returns[key] = [b / a - ONE for a, b in zip(values, values[1:])]
    if reasons:
        return None, reasons
    means = {key: sum(values, ZERO) / dec(str(n)) for key, values in returns.items()}
    annual = dec(str(config["annualization_sessions"]))
    matrix = {}
    for a in ids:
        for b in ids:
            matrix[a, b] = annual * sum(
                ((x - means[a]) * (y - means[b]) for x, y in zip(returns[a], returns[b])), ZERO
            ) / dec(str(n - 1))
    return matrix, []


def cross(left, right, matrix):
    return sum((left[a] * right[b] * matrix[a, b]
                for a in left for b in right), ZERO)


def variance(weights, matrix):
    value = cross(weights, weights, matrix)
    if value < -dec("0.000000000000000000000001"):
        raise RiskBlocked("NEGATIVE_VARIANCE")
    return max(ZERO, value)


def _cap(weights, floor, ids, limit, reason, adjustments):
    fixed = sum((floor[k] for k in ids), ZERO)
    if fixed > limit:
        raise RiskBlocked(reason + "_PROTECTED_OR_LOCKED_FLOOR")
    extra = sum((weights[k] - floor[k] for k in ids), ZERO)
    if fixed + extra > limit and extra > ZERO:
        ratio = max(ZERO, (limit - fixed) / extra)
        for key in ids:
            weights[key] = floor[key] + (weights[key] - floor[key]) * ratio
        adjustments.append(reason)


def project(raw, locked, data, config):
    ids = sorted(raw)
    assets = {a["id"]: a for a in data["instruments"]}
    holdings = {h["asset_id"]: h for h in data["portfolio"]["holdings"]}
    equity, p = data["_equity"], data["policy"]
    floor = {key: (holdings[key]["_value"] / equity if key in locked else
                   dec(holdings[key]["protected_units"]) *
                   dec(holdings[key]["mark"]["value"]) / equity) for key in ids}
    weights = {key: max(raw[key], floor[key]) for key in ids}
    adjustments = []
    try:
        for key in ids:
            _cap(weights, floor, [key], dec(p["max_asset_weight"]),
                 "ASSET_CAP:" + key, adjustments)
        for group in sorted(p["max_group_weights"]):
            members = [key for key in ids if assets[key]["risk_group"] == group]
            _cap(weights, floor, members, dec(p["max_group_weights"][group]),
                 "GROUP_CAP:" + group, adjustments)
        invested_cap = min(dec(p["max_invested_weight"]),
                           max(ZERO, ONE - dec(p["cash_reserve"]) / equity))
        _cap(weights, floor, ids, invested_cap, "INVESTED_OR_CASH_RESERVE_CAP", adjustments)
    except RiskBlocked as exc:
        return None, {"status": "BLOCKED", "reasons": [str(exc)]}
    required = [key for key in ids if weights[key] > ZERO or holdings[key]["_value"] > ZERO]
    matrix, missing = covariance(data, required, config)
    if missing:
        return None, {"status": "WAIT", "reasons": missing}
    current = {key: holdings[key]["_value"] / equity for key in required}
    target = {key: weights[key] for key in required}
    limit = dec(p["volatility_limit"])
    pre_vol = variance(target, matrix).sqrt()
    scale = ONE
    if pre_vol > limit:
        fixed = {key: floor[key] for key in required}
        extra = {key: weights[key] - floor[key] for key in required}
        a, b = variance(extra, matrix), cross(fixed, extra, matrix)
        c = variance(fixed, matrix) - limit * limit
        if a == ZERO:
            return None, {"status": "BLOCKED", "reasons": ["VOLATILITY_SCALING_PATH_INFEASIBLE"]}
        discriminant = b * b - a * c
        if discriminant < ZERO:
            return None, {"status": "BLOCKED", "reasons": ["VOLATILITY_SCALING_PATH_INFEASIBLE"]}
        root = discriminant.sqrt()
        lower, upper = (-b - root) / a, (-b + root) / a
        scale = min(ONE, upper)
        if scale < max(ZERO, lower):
            return None, {"status": "BLOCKED", "reasons": ["VOLATILITY_SCALING_PATH_INFEASIBLE"]}
        for key in required:
            weights[key] = fixed[key] + scale * extra[key]
        adjustments.append("PORTFOLIO_VOLATILITY_CAP")
    target = {key: weights[key] for key in required}
    post_vol = variance(target, matrix).sqrt()
    if post_vol > limit + dec("0.000000000001"):
        return None, {"status": "BLOCKED", "reasons": ["NUMERIC_VOLATILITY_BOUND"]}
    return weights, {
        "status": "TARGET_FEASIBLE",
        "reasons": adjustments,
        "current_annualized_volatility": variance(current, matrix).sqrt(),
        "target_volatility_before_scaling": pre_vol,
        "target_annualized_volatility": post_vol,
        "volatility_limit": limit,
        "extra_allocation_scale": scale,
        "protected_or_locked_floors": floor,
        "cash_weight": ONE - sum(weights.values(), ZERO),
        "note": "Target constraint only; partial reference adjustments do not prove current risk is within limit."
    }
