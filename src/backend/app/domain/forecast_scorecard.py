"""Read-only scoring of frozen predictions against their first formal outcome.

This module never fits a model or substitutes today's estimate for an old forecast.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.domain.finance import SHANGHAI, ZERO

RESULT_BASIS = "VERIFIED_FORMAL_NAV_AND_RECONSTRUCTED_TOTAL_RETURN"
HORIZONS = (1, 5)
RECENT_LIMIT = 20
DIRECTION_NOTE = "预测和实际均非零时比较正负方向；零预测为未给方向、实际平盘不计方向命中，二者均不进入分母。"
NOTICE = "只累计当时冻结后取得的首次正式结果；回顾性历史测试单列。误差较小不等于交易盈利，少量或重叠样本不能证明稳定有效。"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    try:
        result = Decimal(value)
        return result if result.is_finite() else None
    except (ValueError, InvalidOperation):
        return None


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _sort_time(value):
    return _time(value) or datetime.min.replace(tzinfo=SHANGHAI)


def _prediction_eligible(p, fund, now):
    generated, dataset, baseline = (_time(p.get(k)) for k in
                                    ("generated_at", "dataset_observed_at", "base_observed_at"))
    target, base = _day(p.get("target_date")), _day(p.get("base_date"))
    return bool(p.get("id") and p.get("prediction_id") == p["id"] and
                p.get("task") == "TOTAL_RETURN" and p.get("status") == "READY" and
                p.get("target_existed_at_generation") is False and
                p.get("engine_version") and type(p.get("horizon")) is int and p["horizon"] in HORIZONS and
                fund and p.get("fund_code") == fund.get("fund_code") and
                generated and dataset and baseline and now and dataset <= generated <= now and baseline <= generated and
                base and target and base < target and generated.astimezone(SHANGHAI).date() <= target and
                _number(p.get("forecast_return")) is not None)


def _resolution_eligible(p, r, now):
    generated = _time(p["generated_at"])
    resolved, first_seen, research = (_time(r.get(k)) for k in
                                    ("resolved_at", "actual_first_observed_at", "research_observed_at"))
    return bool(r.get("id") == p["id"] and r.get("prediction_id") == p["id"] and
                all(r.get(k) == p.get(k) for k in ("engine_version", "fund_id", "fund_code", "horizon", "target_date")) and
                r.get("basis") == RESULT_BASIS and r.get("actual_observation_id") and
                resolved and first_seen and research and generated < first_seen <= resolved <= now and
                research <= resolved and _number(r.get("actual_return")) is not None)


def _resolved_row(p, r):
    predicted, actual = _number(p["forecast_return"]), _number(r["actual_return"])
    error = (predicted - actual) * 100
    baseline_error = abs(actual) * 100
    low, high = _number(p.get("lower_return")), _number(p.get("upper_return"))
    return {"prediction_id": p["id"], "fund_id": p["fund_id"], "fund_code": p["fund_code"],
            "engine_version": p["engine_version"], "horizon": p["horizon"],
            "base_date": p["base_date"], "target_date": p["target_date"], "generated_at": p["generated_at"],
            "resolved_at": r["resolved_at"], "actual_first_observed_at": r["actual_first_observed_at"],
            "selected_model": p.get("selected_model"), "model_label": p.get("model_label") or p.get("label"),
            "forecast_return": predicted, "actual_return": actual, "baseline_return": ZERO,
            "error_pp": error, "absolute_error_pp": abs(error), "baseline_absolute_error_pp": baseline_error,
            "improvement_pp": baseline_error - abs(error),
            "direction_correct": (predicted > 0) == (actual > 0) if predicted != 0 and actual != 0 else None,
            "forecast_flat": predicted == 0, "actual_flat": actual == 0,
            "interval_covered": low <= actual <= high if low is not None and high is not None and low <= high else None,
            "actual_observation_id": r["actual_observation_id"], "actual_nav": _number(r.get("actual_nav"))}


def _metrics(rows):
    n = len(rows)
    mae = sum((r["absolute_error_pp"] for r in rows), ZERO) / n if n else None
    baseline = sum((r["baseline_absolute_error_pp"] for r in rows), ZERO) / n if n else None
    direction = [r["direction_correct"] for r in rows if r["direction_correct"] is not None]
    intervals = [r["interval_covered"] for r in rows if r["interval_covered"] is not None]
    return {"resolved": n, "mae_pp": mae, "baseline_mae_pp": baseline,
            "improvement_pp": baseline - mae if n else None,
            "relative_improvement": (baseline - mae) / baseline if n and baseline else None,
            "verdict": "NO_RESULT" if not n else "IMPROVED" if mae < baseline else "TIED" if mae == baseline else "WORSE",
            "direction_count": len(direction), "direction_correct_count": sum(direction),
            "direction_accuracy": Decimal(sum(direction)) / len(direction) if direction else None,
            "forecast_flat_count": sum(r["forecast_flat"] for r in rows),
            "actual_flat_count": sum(r["actual_flat"] for r in rows),
            "interval_count": len(intervals), "interval_coverage": Decimal(sum(intervals)) / len(intervals) if intervals else None}


def build_forecast_scorecard(funds, predictions, resolutions, *, now, engine_version, supported_codes):
    """Pure aggregation, with first-issued origin and first-resolution semantics.

    Input lists can contain duplicate transport copies. A revised input hash for
    the same engine/fund/base/target/horizon remains one forecast opportunity;
    the earliest frozen issue is kept, never the most accurate later revision.
    """
    clock = _time(now)
    supported = {f["id"]: f for f in funds if f.get("fund_code") in supported_codes and f.get("share_class") == "C"}
    unique, origins, eligible, exclusions = set(), set(), [], Counter()
    for p in sorted(predictions, key=lambda r: (_sort_time(r.get("generated_at")), str(r.get("id", "")))):
        if p.get("id") in unique:
            exclusions["duplicate_copy"] += 1
            continue
        unique.add(p.get("id"))
        if not _prediction_eligible(p, supported.get(p.get("fund_id")), clock):
            exclusions["not_forward_eligible"] += 1
            continue
        origin = tuple(p.get(k) for k in ("engine_version", "fund_id", "horizon", "base_date", "target_date"))
        if origin in origins:
            exclusions["later_revision"] += 1
            continue
        origins.add(origin)
        eligible.append(p)
    first_resolutions = {}
    for r in sorted(resolutions, key=lambda r: (_sort_time(r.get("resolved_at")), str(r.get("id", "")))):
        first_resolutions.setdefault(r.get("prediction_id") or r.get("id"), r)
    by_group = {}
    # Empty current-version rows distinguish no samples from missing experiments.
    for fid in supported:
        for horizon in HORIZONS:
            by_group[(engine_version, fid, horizon)] = []
    for p in eligible:
        by_group.setdefault((p["engine_version"], p["fund_id"], p["horizon"]), []).append(p)
    groups, all_resolved, historical_tests = [], [], []
    for (engine, fid, horizon), values in sorted(by_group.items()):
        matched, invalid = [], 0
        for p in values:
            r = first_resolutions.get(p["id"])
            if r and _resolution_eligible(p, r, clock):
                matched.append(_resolved_row(p, r))
            elif r:
                invalid += 1
        matched.sort(key=lambda r: (r["target_date"], _sort_time(r["generated_at"]), r["prediction_id"]), reverse=True)
        f = supported[fid]
        group = {"engine_version": engine, "fund_id": fid, "fund_code": f["fund_code"], "name": f.get("name"),
                 "horizon": horizon, "count": len(values), "pending": len(values) - len(matched),
                 "unverified_result_count": invalid, **_metrics(matched),
                 "overlapping_targets": horizon > 1, "recent_resolved": matched[:RECENT_LIMIT]}
        groups.append(group)
        all_resolved.extend(matched)
        latest = max(values, key=lambda p: (_sort_time(p["generated_at"]), p["id"]), default=None)
        test = (latest or {}).get("test")
        if isinstance(test, dict) and type(test.get("count")) is int and test["count"] > 0:
            historical_tests.append({"engine_version": engine, "fund_id": fid, "fund_code": f["fund_code"],
                "horizon": horizon, "prediction_id": latest["id"], "generated_at": latest["generated_at"],
                "scope": "RETROSPECTIVE_RECONSTRUCTED_FINAL_TEST", "count": test["count"],
                "start": test.get("start"), "end": test.get("end"), "selected_model": latest.get("selected_model"),
                "mae_pp": _number(test.get("mae")) * 100 if _number(test.get("mae")) is not None else None,
                "baseline_mae_pp": _number(test.get("baseline_mae")) * 100 if _number(test.get("baseline_mae")) is not None else None,
                "notice": "本组最新冻结预测附带的回顾性最终测试，不计入前向成绩，测试后不据此改选模型。"})
    all_resolved.sort(key=lambda r: (r["target_date"], _sort_time(r["generated_at"]), r["prediction_id"]), reverse=True)
    unsupported = [{"fund_id": f["id"], "fund_code": f.get("fund_code"), "name": f.get("name"),
                    "status": "NOT_IN_EXPERIMENT", "message": "尚未纳入六基金预测实验；行情与持仓可正常使用。"}
                   for f in funds if f["id"] not in supported]
    return {"engine_version": engine_version, "as_of": now, "status": "READY" if all_resolved else "WAIT_RESULTS",
            "horizons": list(HORIZONS), "groups": groups, "historical_tests": historical_tests,
            "recent_resolved": all_resolved[:RECENT_LIMIT], "not_in_experiment": unsupported,
            "summary": {"fund_count": len(supported), "resolved": len(all_resolved),
                        "pending": len(eligible) - len(all_resolved), "prediction_count": len(eligible)},
            "excluded": dict(exclusions), "recent_limit": RECENT_LIMIT,
            "direction_note": DIRECTION_NOTE, "notice": NOTICE,
            "baseline": {"label": "零收益基线", "return": ZERO,
                         "definition": "同一期限始终预测总回报为0；基线MAE为相同已核对样本的实际总回报绝对值。"},
            "deduplication": "按引擎、基金、期限、基准日和目标日保留首次冻结预测；只采用该预测的首次正式核对，后续修订不重复计分。"}
