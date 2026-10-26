"""Small, fixed total-return models and retrospective rolling-origin evaluation.

The supplied rows are a current public reconstruction, not a historical
publication archive. Each fold uses its known prefix, but that alone does not
make this a point-in-time trading backtest. No probability or order is emitted.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


ENGINE_VERSION = "funddesk-forecast-v1"
ZERO, ONE = Decimal("0"), Decimal("1")
PRECISION = Decimal("0.000000000001")
MODELS = [
    ("ZERO", "零收益基线"),
    ("SHRUNK_MEAN20", "20期均值收缩"),
    ("SHRUNK_MEAN60", "60期均值收缩"),
    ("AR1_RIDGE120", "120期正则自回归"),
]


def _config(horizon):
    return {"max_points": 1000, "minimum_points": 430, "minimum_training_returns": 126,
            "training_return_window": 252, "mean_windows": [20, 60], "mean_shrink": Decimal("0.5"),
            "ar1_return_window": 120, "ar1_ridge": Decimal("0.001"),
            "ar1_coefficient_min": Decimal("-0.8"), "ar1_coefficient_max": Decimal("0.8"),
            "ar1_daily_return_min": Decimal("-0.25"), "ar1_daily_return_max": Decimal("0.25"),
            "validation_targets": 100, "calibration_targets": 100, "test_targets": 100,
            "purge_targets": horizon, "selection_minimum_improvement": Decimal("0.02"),
            "selection_metric": "VALIDATION_MAE", "interval_level": Decimal("0.8"),
            "minimum_calibration_count": 20, "interval_method": "EMPIRICAL_ABSOLUTE_ERROR",
            "interval_return_floor": Decimal("-1"), "output_decimal_places": 12,
            "data_basis": "PUBLIC_RECONSTRUCTION_RETROSPECTIVE_ROLLING",
            "horizon_basis": "VALIDATED_INPUT_SESSION_STEPS",
            "direction_definition": "实际为非零收益的测试点；零预测不视为上涨或下跌，实际平盘不计方向命中。"}


def _rounded(value):
    return value.quantize(PRECISION, rounding=ROUND_HALF_UP)


def _number(value):
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise ValueError("净值与总回报指数须为十进制数，不能缺值或传入浮点数")
    if isinstance(value, str) and len(value) > 80:
        raise ValueError("历史数值格式无效")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("历史数值格式无效")
    if not result.is_finite() or result <= ZERO or result >= Decimal("1e15") or result.as_tuple().exponent < -36:
        raise ValueError("历史净值或总回报指数无效")
    return result


def _series(rows):
    if not isinstance(rows, list):
        raise ValueError("请提供按日期排序的历史序列")
    result, prior = [], ""
    for row in rows[-1000:]:
        if not isinstance(row, (list, tuple)) or len(row) != 3 or not isinstance(row[0], str):
            raise ValueError("历史序列须为日期、净值和总回报指数三列")
        try:
            on = date.fromisoformat(row[0]).isoformat()
        except ValueError:
            raise ValueError("历史日期格式无效")
        if on != row[0] or on <= prior:
            raise ValueError("历史日期须唯一并按时间递增")
        result.append((on, _number(row[1]), _number(row[2])))
        prior = on
    return result


def _predictions(known_returns, horizon, config):
    """All inputs end at the issue origin; targets never enter model fitting."""
    forecasts = {"ZERO": ZERO}
    for window in (20, 60):
        recent = known_returns[-window:]
        daily = config["mean_shrink"] * sum(recent, ZERO) / len(recent)
        forecasts["SHRUNK_MEAN" + str(window)] = _rounded((ONE + daily) ** horizon - ONE)
    recent = known_returns[-config["ar1_return_window"]:]
    pairs = list(zip(recent[:-1], recent[1:]))
    count = Decimal(len(pairs))
    x_mean = sum((x for x, _ in pairs), ZERO) / count
    y_mean = sum((y for _, y in pairs), ZERO) / count
    variance = sum(((x - x_mean) ** 2 for x, _ in pairs), ZERO)
    covariance = sum(((x - x_mean) * (y - y_mean) for x, y in pairs), ZERO)
    coefficient = covariance / (variance + config["ar1_ridge"])
    coefficient = min(config["ar1_coefficient_max"], max(config["ar1_coefficient_min"], coefficient))
    intercept = y_mean - coefficient * x_mean
    last, compounded = recent[-1], ONE
    for _ in range(horizon):
        last = min(config["ar1_daily_return_max"], max(config["ar1_daily_return_min"], intercept + coefficient * last))
        compounded *= ONE + last
    forecasts["AR1_RIDGE120"] = _rounded(compounded - ONE)
    return forecasts


def _improvement(baseline, selected):
    if baseline == ZERO:
        return ZERO if selected == ZERO else None
    return _rounded((baseline - selected) / baseline)


def _bounds(prediction, half_width):
    if half_width is None:
        return None, None
    return max(Decimal("-1"), prediction - half_width), prediction + half_width


def _empty(horizon, config, reason, history=None):
    return {"status": "INSUFFICIENT", "horizon": horizon, "engine_version": ENGINE_VERSION,
            "selected_model": None, "label": "样本或资料待补充", "selection_reason": reason,
            "forecast_return": None, "lower_return": None, "upper_return": None,
            "validation": {"start": None, "end": None, "count": 0, "selected_mae": None,
                           "baseline_mae": None, "improvement": None},
            "calibration": {"count": 0, "raw_count": 0, "level": config["interval_level"], "half_width": None},
            "test": {"start": None, "end": None, "count": 0, "mae": None, "rmse": None,
                     "baseline_mae": None, "improvement": None, "direction_accuracy": None,
                     "direction_count": 0, "mean_bias": None, "coverage": None,
                     "interval_width": None, "nonoverlap_count": 0, "overlapping_targets": horizon == 5,
                     "improved_over_baseline": None},
            "candidates": [], "history": history or {"start": None, "end": None, "count": 0},
            "folds": [], "config": config,
            "notice": "公开重建历史的回顾性滚动测试，不是历史时点可交易实测或上涨概率。"}


def fit_forecast(rows, horizon):
    """Fit on prefixes, select on validation, calibrate, then report untouched tests.

    Indices describe target dates, with N retained points and h steps:
      validation [N-300-2h, N-200-2h)
      calibration [N-200-h, N-100-h)
      test        [N-100, N)
    Each interval has 100 targets and each inter-interval purge has h targets.
    The issue origin for target t is t-h, so the first origin needs 126 known
    daily returns: h=1 requires 430 points, h=5 requires 441 points.
    The caller supplies a validated session series; this module cannot infer
    missing product sessions or fund dealing dates from weekday arithmetic.
    """
    valid_horizon = type(horizon) is int and horizon in {1, 5}
    config = _config(horizon if valid_horizon else None)
    if not valid_horizon:
        return _empty(horizon, config, "预测期限只支持1期或5期")
    try:
        series = _series(rows)
    except ValueError as exc:
        return _empty(horizon, config, str(exc))
    history = {"start": series[0][0] if series else None, "end": series[-1][0] if series else None,
               "count": len(series), "source_count": len(rows)}
    minimum = max(config["minimum_points"], config["minimum_training_returns"] + 300 + 3 * horizon)
    config["required_points_for_horizon"] = minimum
    if len(series) < minimum:
        return _empty(horizon, config, f"该期限至少需要{minimum}个有效点，才能保留训练、验证、校准与最终测试及隔离区间", history)
    try:
        with localcontext() as context:
            context.prec = 36
            return _fit(series, horizon, config, history)
    except ArithmeticError:
        return _empty(horizon, config, "历史数值变动异常，暂不生成预测", history)


def _fit(series, horizon, config, history):
    count = len(series)
    segments = {"validation": (count - 300 - 2 * horizon, count - 200 - 2 * horizon),
                "calibration": (count - 200 - horizon, count - 100 - horizon),
                "test": (count - 100, count)}
    config["segments"] = {name: {"target_start_index": a, "target_end_index_exclusive": b}
                          for name, (a, b) in segments.items()}
    first_origin = segments["validation"][0] - horizon
    first_training = max(0, first_origin - config["training_return_window"])
    history["train"] = {"start": series[first_training][0], "end": series[first_origin][0],
                        "count": first_origin - first_training + 1, "return_count": first_origin - first_training}
    returns = [b[2] / a[2] - ONE for a, b in zip(series, series[1:])]

    def folds(start, end):
        output = []
        for target in range(start, end):
            origin = target - horizon
            known = returns[max(0, origin - config["training_return_window"]):origin]
            predictions = _predictions(known, horizon, config)
            output.append({"date": series[target][0], "origin_date": series[origin][0],
                           "actual_return": _rounded(series[target][2] / series[origin][2] - ONE),
                           "predictions": predictions})
        return output

    validation = folds(*segments["validation"])
    validation_mae = {model: sum((abs(f["predictions"][model] - f["actual_return"]) for f in validation), ZERO) / len(validation)
                      for model, _ in MODELS}
    baseline_mae = validation_mae["ZERO"]
    best = min((model for model, _ in MODELS), key=lambda model: validation_mae[model])
    gain = (baseline_mae - validation_mae[best]) / baseline_mae if baseline_mae else ZERO
    selected = best if best != "ZERO" and gain is not None and gain >= config["selection_minimum_improvement"] else "ZERO"
    # Lock model choice before evaluating calibration or final-test targets.
    selected_label = dict(MODELS)[selected]
    selection_reason = ("验证段MAE较零收益基线改善至少2%，采用验证段误差最低的固定候选。" if selected != "ZERO" else
                        "验证段未达到较零收益基线改善2%的门槛，保留零收益基线。")
    calibration = folds(*segments["calibration"])
    independent_calibration = calibration[::horizon]
    errors = sorted(abs(f["predictions"][selected] - f["actual_return"]) for f in independent_calibration)
    half_width = None
    if len(errors) >= config["minimum_calibration_count"]:
        rank = (len(errors) * 80 + 99) // 100
        half_width = errors[rank - 1]
    test = folds(*segments["test"])
    test_errors = [f["predictions"][selected] - f["actual_return"] for f in test]
    mae = sum((abs(e) for e in test_errors), ZERO) / len(test)
    baseline_test_mae = sum((abs(f["actual_return"]) for f in test), ZERO) / len(test)
    directional = [f for f in test if f["actual_return"] != ZERO]
    direction_hits = sum((f["predictions"][selected] > ZERO and f["actual_return"] > ZERO) or
                         (f["predictions"][selected] < ZERO and f["actual_return"] < ZERO) for f in directional)
    output_folds, covered, widths = [], 0, []
    for f in test:
        predicted = f["predictions"][selected]
        lower, upper = _bounds(predicted, half_width)
        output_folds.append({"date": f["date"], "origin_date": f["origin_date"],
                             "forecast_return": predicted, "baseline_return": ZERO,
                             "actual_return": f["actual_return"], "lower_return": lower, "upper_return": upper})
        if lower is not None:
            covered += lower <= f["actual_return"] <= upper
            widths.append(upper - lower)
    current = _predictions(returns[-config["training_return_window"]:], horizon, config)[selected]
    lower, upper = _bounds(current, half_width)
    return {"status": "READY", "horizon": horizon, "engine_version": ENGINE_VERSION,
            "selected_model": selected, "label": selected_label, "selection_reason": selection_reason,
            "forecast_return": current, "lower_return": lower, "upper_return": upper,
            "validation": {"start": validation[0]["date"], "end": validation[-1]["date"], "count": len(validation),
                           "selected_mae": _rounded(validation_mae[selected]), "baseline_mae": _rounded(baseline_mae),
                           "improvement": _improvement(baseline_mae, validation_mae[selected])},
            "calibration": {"start": calibration[0]["date"], "end": calibration[-1]["date"],
                            "count": len(errors), "raw_count": len(calibration), "level": config["interval_level"],
                            "half_width": half_width, "nonoverlapping": True,
                            "notice": "校准段绝对误差80%经验半宽；非概率保证。"},
            "test": {"start": test[0]["date"], "end": test[-1]["date"], "count": len(test),
                     "mae": _rounded(mae), "rmse": _rounded((sum((e * e for e in test_errors), ZERO) / len(test)).sqrt()),
                     "baseline_mae": _rounded(baseline_test_mae), "improvement": _improvement(baseline_test_mae, mae),
                     "improved_over_baseline": mae < baseline_test_mae,
                     "direction_accuracy": _rounded(Decimal(direction_hits) / len(directional)) if directional else None,
                     "direction_count": len(directional), "actual_flat_count": len(test) - len(directional),
                     "forecast_flat_count": sum(f["predictions"][selected] == ZERO for f in test),
                     "mean_bias": _rounded(sum(test_errors, ZERO) / len(test)),
                     "coverage": _rounded(Decimal(covered) / len(test)) if widths else None,
                     "interval_width": _rounded(sum(widths, ZERO) / len(widths)) if widths else None,
                     "nonoverlap_count": len(test[::horizon]), "overlapping_targets": horizon > 1,
                     "notice": "已锁定验证段选型；最终测试未改善也不会据此改选模型。"},
            "candidates": [{"model": model, "label": label, "validation_mae": _rounded(validation_mae[model]),
                            "test_mae": _rounded(sum((abs(f["predictions"][model] - f["actual_return"]) for f in test), ZERO) / len(test))}
                           for model, label in MODELS],
            "history": history, "folds": output_folds, "config": config,
            "notice": "公开重建历史的回顾性滚动测试，不是历史时点可交易实测或上涨概率；尚未验证费用后交易收益。"}
