"""Independent, versioned total-return experiments; never changes financial facts."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from threading import Lock
import base64
import copy
import gzip
import json

from app.domain.finance import Problem, SHANGHAI, ZERO, money, require, stamp
from app.domain.forecasting import ENGINE_VERSION, fit_forecast
from app.domain.research_calendar import reference_sessions
from app.persistence.store import Store, digest, pack
from app.providers.research_history import PRODUCTS

RUN_LIMIT = 10
HORIZONS = (1, 5)
FORMAL_QUALITIES = {"DISTRIBUTOR_DISCLOSURE", "MANAGER_DISCLOSURE"}
MODEL_LABELS = {"ZERO": "零收益基线", "ZERO_RETURN": "零收益基线", "LAST_VALUE": "零收益基线",
                "MEAN20": "20期均值", "MEAN60": "60期均值", "MEAN_20": "20期均值", "MEAN_60": "60期均值",
                "SHRUNK_MEAN20": "20期收缩均值", "SHRUNK_MEAN60": "60期收缩均值", "AR1_RIDGE120": "120期正则自回归",
                "MOMENTUM20": "20期趋势", "MOMENTUM_20": "20期趋势", "AR1": "一阶自回归"}
NOTICE = "独立总回报研究，不自动下单，也不改变正式收益。历史重建检验不等于发布后的真实预测成绩。"
HOLDING_ASSUMPTION = "按当前已确认份额保持不变、期间无分红或拆分计算的价格效应参考，未扣交易费用；不是实际盈亏或投资建议。"
NAV_ASSUMPTION = "总回报折算参考；假设预测期间没有分红或拆分，不保证与实际单位净值一致。"


def _num(value):
    """Research TRI/returns intentionally retain more precision than money input."""
    require(isinstance(value, (Decimal, str, int)) and not isinstance(value, bool), "INVALID_FORECAST_VALUE", "预测数值须使用十进制")
    try:
        result = Decimal(value)
    except (ValueError, InvalidOperation):
        raise Problem("INVALID_FORECAST_VALUE", "预测数值无效")
    require(result.is_finite(), "INVALID_FORECAST_VALUE", "预测数值无效")
    return result


def _compress(value):
    return base64.b64encode(gzip.compress(pack(value).encode(), mtime=0)).decode()


def _expand(value):
    return json.loads(gzip.decompress(base64.b64decode(value)))


def _summary_model(model):
    return {k: copy.deepcopy(v) for k, v in model.items() if k not in {"history", "folds", "candidates", "config"}}


class ForecastingMixin:
    def _forecast_lock(self):
        return self.__dict__.setdefault("_forecast_generation_lock", Lock())

    def _forecast_context(self, c):
        snapshot = self.snapshot(c)
        datasets = self.research_datasets(c)
        basis = []
        for f in snapshot["funds"]:
            if f.get("fund_code") not in PRODUCTS or f.get("share_class") != "C":
                continue
            data, nav = datasets.get(f["fund_code"]), f.get("nav")
            basis.append({"fund_id": f["id"], "fund_code": f["fund_code"],
                "nav": {k: nav.get(k) for k in ("id", "effective_date", "value", "quality")} if nav else None,
                "history_hash": digest(data["rows"]) if data else None,
                "action_hash": digest(data.get("actions", [])) if data else None,
                "corporate_actions_verified": data.get("corporate_actions_verified") if data else None,
                "series_kind": data.get("series_kind") if data else None})
        return snapshot, datasets, {"engine_version": ENGINE_VERSION, "mode": self.mode, "funds": basis}

    @staticmethod
    def _forecast_inputs(fund, data, now):
        nav = fund.get("nav")
        if fund.get("fund_code") not in PRODUCTS or fund.get("share_class") != "C":
            return None, "SOURCE_NOT_SUPPORTED", "当前预测只对已经独立核实的六只 C 类基金开放"
        if not nav or nav.get("quality") not in FORMAL_QUALITIES:
            return None, "FORMAL_NAV_REQUIRED", "等待正式披露来源净值作为预测基准"
        if stamp(nav["observed_at"]) > stamp(now):
            return None, "FUTURE_BASIS", "净值首见时点晚于本次生成时点"
        if (not data or data.get("series_kind") != "FUND_TOTAL_RETURN" or
                data.get("corporate_actions_verified") is not True):
            return None, "HISTORY_REQUIRED", "等待核实净值与分红重建的总回报历史"
        if not data.get("observed_at") or stamp(data["observed_at"]) > stamp(now):
            return None, "HISTORY_TIME_INVALID", "历史资料取得时点缺失或晚于生成时点"
        raw_rows = data.get("rows", [])
        rows = [r for r in raw_rows if r[0] <= nav["effective_date"]]
        if (not raw_rows or raw_rows[-1][0] != nav["effective_date"] or
                _num(raw_rows[-1][1]) != _num(nav["value"])):
            return None, "HISTORY_NAV_MISMATCH", "公开历史尾日与正式基准不一致，等待两类来源对齐"
        if not rows or rows[-1][0] != nav["effective_date"] or _num(rows[-1][1]) != _num(nav["value"]):
            return None, "HISTORY_NAV_MISMATCH", "历史尾日或尾部净值与正式基准不一致，保留等待"
        return rows, None, None

    def _forecast_target(self, c, fund_id, base_date, horizon, business_date):
        sessions = [d for d in reference_sessions() if d > base_date]
        if len(sessions) < horizon:
            return None, "CALENDAR_MISSING", "参考交易日日历尚未覆盖预测期限"
        target = sessions[horizon - 1]
        if target < business_date:
            return target, "BASE_EXPIRED", "基准较旧，对应目标日已过；等待新的正式净值"
        existing = c.execute("SELECT 1 FROM observations WHERE fund_id=? AND value_kind='OFFICIAL_NAV' AND effective_date=? LIMIT 1",
                             (fund_id, target)).fetchone()
        if existing:
            return target, "TARGET_ALREADY_OBSERVED", "目标日净值已被观察，不回填为未来预测"
        return target, None, None

    def generate_forecasts(self, p=None, *, automatic=False):
        p = p or {}
        require(isinstance(p, dict) and set(p) <= {"key", "refresh"}, "INVALID_FORECAST_REQUEST", "预测请求包含未知字段")
        refresh = p.get("refresh", True)
        require(isinstance(refresh, bool), "INVALID_FORECAST_REQUEST", "刷新选项须为布尔值")
        if "key" in p:
            require(isinstance(p["key"], str) and 8 <= len(p["key"]) <= 160, "IDEMPOTENCY_REQUIRED", "预测请求标识须为 8–160 字符")
        require(self.mode == "real", "REAL_ONLY", "本预测实验使用真实账户的独立公开历史；演示研究保持原样")
        gate = self._forecast_lock()
        require(gate.acquire(blocking=False), "FORECAST_IN_PROGRESS", "预测正在生成，请稍后查看")
        try:
            refresh_status = None
            if refresh:
                try:
                    refresh_status = self.refresh_research_data()
                except Problem as exc:
                    refresh_status = {"status": "WAIT", "code": exc.code, "message": exc.message}
            with self.store.connect() as c:
                c.execute("BEGIN")
                snapshot, datasets, basis = self._forecast_context(c)
                now, today = snapshot["now"], snapshot["business_date"]
                basis_hash = digest(basis)
                run_id = "forecast_" + digest([ENGINE_VERSION, self.mode, basis_hash, today])[:24]
                old = Store.doc(c, "forecast_run", run_id)
                if old:
                    return dict(self._forecast_run_view(c, old, basis_hash, snapshot), duplicate=True,
                                refresh_status=refresh_status)
                prepared = []
                for fund in snapshot["funds"]:
                    data = datasets.get(fund.get("fund_code"))
                    rows, code, message = self._forecast_inputs(fund, data, now)
                    targets = []
                    for horizon in HORIZONS:
                        target, target_code, target_message = self._forecast_target(c, fund["id"], fund["nav"]["effective_date"], horizon, today) if rows else (None, None, None)
                        targets.append((horizon, target, target_code, target_message))
                    prepared.append((fund, data, rows, code, message, targets))
            # Download and expensive rolling fits deliberately occur outside any write transaction.
            funds, inputs, predictions = [], {}, []
            for fund, data, rows, code, message, targets in prepared:
                forecasts = []
                if rows:
                    inputs[fund["id"]] = {"fund_code": fund["fund_code"], "rows": rows,
                        "actions": [a for a in data.get("actions", []) if a.get("date", "") <= rows[-1][0]],
                        "observed_at": data["observed_at"], "source": data.get("source"), "source_url": data.get("source_url"),
                        "raw_sha256": data.get("raw_sha256"), "base_observation": fund["nav"]}
                for horizon, target, target_code, target_message in targets:
                    if not rows or target_code:
                        forecasts.append({"horizon": horizon, "status": "WAIT", "target_date": target,
                            "code": code or target_code, "message": message or target_message,
                            "base_date": (fund.get("nav") or {}).get("effective_date"), "resolution": None})
                        continue
                    model = fit_forecast(rows, horizon)
                    if model.get("status") != "READY":
                        forecasts.append(dict(_summary_model(model), horizon=horizon, target_date=target,
                            status="WAIT", code=model.get("code", "MODEL_NOT_READY"), resolution=None,
                            message=model.get("selection_reason") or "样本或资料待补充",
                            history=copy.deepcopy(model.get("history", {})), base_date=rows[-1][0]))
                        continue
                    history_hash = digest(rows)
                    prediction_id = "fp_" + digest([ENGINE_VERSION, self.mode, fund["id"], history_hash,
                        rows[-1][0], rows[-1][1], horizon, target])[:28]
                    prediction = dict(_summary_model(model), prediction_id=prediction_id, id=prediction_id,
                        fund_id=fund["id"], fund_code=fund["fund_code"], engine_version=ENGINE_VERSION,
                        horizon=horizon, status="READY", base_date=rows[-1][0], base_nav=str(rows[-1][1]),
                        base_observation_id=fund["nav"]["id"], base_observed_at=fund["nav"]["observed_at"],
                        target_date=target, target_calendar_kind="CN_REFERENCE_SESSIONS_NOT_FUND_OPEN_DATES",
                        generated_at=now, dataset_observed_at=data["observed_at"], input_hash=history_hash,
                        history_basis_hash=digest({"rows": rows, "actions": inputs[fund["id"]]["actions"]}),
                        task="TOTAL_RETURN", target_existed_at_generation=False,
                        model_config=copy.deepcopy(model.get("config", {})),
                        predicted_nav_reference=_num(rows[-1][1]) * (1 + _num(model["forecast_return"])),
                        nav_reference_assumption=NAV_ASSUMPTION,
                        model_label=model.get("label") or MODEL_LABELS.get(model.get("selected_model"), model.get("selected_model")),
                        history=dict(model.get("history", {}), **{
                            "count": model.get("history", {}).get("count", len(rows)),
                            "start": model.get("history", {}).get("start", rows[0][0]),
                            "end": model.get("history", {}).get("end", rows[-1][0]),
                            "source_count": len(rows), "source_start": rows[0][0], "source_end": rows[-1][0],
                            "observed_at": data["observed_at"], "basis": data.get("basis"), "source": data.get("source")}))
                    predictions.append(prediction)
                    forecasts.append(dict({k: v for k, v in prediction.items() if k != "model_config"}, resolution=None))
                    inputs[fund["id"]].setdefault("models", {})[str(horizon)] = model
                ready = sum(f["status"] == "READY" for f in forecasts)
                funds.append({"fund_id": fund["id"], "fund_code": fund.get("fund_code"), "name": fund["name"],
                    "status": "READY" if ready == len(HORIZONS) else "PARTIAL" if ready else "WAIT",
                    "reasons": list(dict.fromkeys(f.get("message") for f in forecasts if f.get("message"))), "forecasts": forecasts})
            status = "READY" if funds and all(f["status"] == "READY" for f in funds) else "PARTIAL" if predictions else "WAIT"
            result = {"id": run_id, "created_at": now, "engine_version": ENGINE_VERSION, "mode": self.mode,
                "status": status, "basis_hash": basis_hash, "business_date": today,
                "funds": funds, "notice": NOTICE, "execution_enabled": False,
                "data_basis": "CURRENT_RECONSTRUCTED_PUBLIC_TOTAL_RETURN", "refresh_status": refresh_status}
            with self.store.transaction() as c:
                current, _, current_basis = self._forecast_context(c)
                require(digest(current_basis) == basis_hash, "FORECAST_BASIS_CHANGED", "正式净值或研究历史已更新，请重新生成")
                old = Store.doc(c, "forecast_run", run_id)
                if old:
                    return dict(self._forecast_run_view(c, old, basis_hash, current), duplicate=True, refresh_status=refresh_status)
                # Recheck target non-observation after a potentially long fit.
                for prediction in predictions:
                    target, target_code, _ = self._forecast_target(c, prediction["fund_id"], prediction["base_date"], prediction["horizon"], self.today())
                    require(target == prediction["target_date"] and not target_code, "FORECAST_TARGET_CHANGED", "计算期间目标日已披露或过期，请刷新后重试")
                    if not Store.doc(c, "forecast_prediction", prediction["id"]):
                        Store.put(c, "forecast_prediction", prediction["id"], prediction, 0)
                for fund_result in result["funds"]:
                    for forecast in fund_result["forecasts"]:
                        if forecast.get("prediction_id"):
                            frozen = Store.doc(c, "forecast_prediction", forecast["prediction_id"])
                            forecast.update({k: v for k, v in frozen.items() if k not in {"_version", "model_config"}})
                sequence = max((r.get("sequence", 0) for r in Store.docs(c, "forecast_run")), default=0) + 1
                record = Store.put(c, "forecast_run", run_id, {"id": run_id, "created_at": now,
                    "sequence": sequence, "engine_version": ENGINE_VERSION, "status": status, "basis_hash": basis_hash,
                    "basis": basis, "fund_count": len(funds), "prediction_count": len(predictions),
                    "result_gzip": _compress(result), "input_gzip": _compress(inputs)}, 0)
                saved = sorted(Store.docs(c, "forecast_run"), key=lambda r: (r["created_at"], r.get("sequence", 0)), reverse=True)
                for expired in saved[RUN_LIMIT:]:
                    c.execute("DELETE FROM documents WHERE kind='forecast_run' AND id=?", (expired["id"],))
                self._resolve_forecasts(c, current, self.research_datasets(c))
                if predictions:
                    old_policy = Store.doc(c, "forecast_automation", "current")
                    Store.put(c, "forecast_automation", "current", {"enabled": True, "engine_version": ENGINE_VERSION,
                        "last_success_at": self.now(), "basis_hash": basis_hash}, old_policy["_version"] if old_policy else 0)
                return dict(self._forecast_run_view(c, record, basis_hash, current), duplicate=False)
        finally:
            gate.release()

    def _resolve_forecasts(self, c, snapshot, datasets):
        funds = {f["id"]: f for f in snapshot["funds"]}
        count = 0
        for prediction in Store.docs(c, "forecast_prediction"):
            if Store.doc(c, "forecast_resolution", prediction["id"]):
                continue
            fund = funds.get(prediction["fund_id"])
            data = datasets.get(prediction["fund_code"])
            if (not fund or not data or data.get("series_kind") != "FUND_TOTAL_RETURN" or
                    data.get("corporate_actions_verified") is not True or not data.get("observed_at") or
                    stamp(data["observed_at"]) > stamp(self.now())):
                continue
            actuals = self.observations(c, fund["id"], "OFFICIAL_NAV")
            dates = {a["effective_date"]: a for a in actuals}
            base, actual = dates.get(prediction["base_date"]), dates.get(prediction["target_date"])
            if (not base or not actual or base.get("quality") not in FORMAL_QUALITIES or
                    actual.get("quality") not in FORMAL_QUALITIES or _num(base["value"]) != _num(prediction["base_nav"])):
                continue
            generated_date = stamp(prediction["generated_at"]).astimezone(SHANGHAI).date().isoformat()
            # First receipt of any formal target matters, not a later corrected receipt.
            first_seen = c.execute("SELECT MIN(observed_at) FROM observations WHERE fund_id=? AND value_kind='OFFICIAL_NAV' AND effective_date=?",
                                   (fund["id"], prediction["target_date"])).fetchone()[0]
            if (prediction["target_date"] < generated_date or prediction.get("target_existed_at_generation") is not False or
                    not first_seen or stamp(first_seen) <= stamp(prediction["generated_at"]) or
                    stamp(actual["observed_at"]) > stamp(self.now())):
                continue
            series = {r[0]: r for r in data.get("rows", [])}
            base_row, target_row = series.get(prediction["base_date"]), series.get(prediction["target_date"])
            if (not base_row or not target_row or _num(base_row[1]) != _num(base["value"]) or
                    _num(target_row[1]) != _num(actual["value"]) or _num(base_row[2]) <= 0):
                continue
            actual_return = _num(target_row[2]) / _num(base_row[2]) - 1
            error = (_num(prediction["forecast_return"]) - actual_return) * 100
            resolution = {"id": prediction["id"], "prediction_id": prediction["id"],
                "engine_version": prediction["engine_version"], "fund_id": fund["id"], "fund_code": fund["fund_code"],
                "horizon": prediction["horizon"], "resolved_at": self.now(), "target_date": prediction["target_date"],
                "actual_return": actual_return, "error_pp": error, "absolute_error_pp": abs(error),
                "baseline_absolute_error_pp": abs(actual_return) * 100,
                "interval_covered": (_num(prediction["lower_return"]) <= actual_return <= _num(prediction["upper_return"]))
                    if prediction.get("lower_return") is not None and prediction.get("upper_return") is not None else None,
                "direction_correct": (_num(prediction["forecast_return"]) > 0) == (actual_return > 0) if actual_return != 0 and _num(prediction["forecast_return"]) != 0 else None,
                "actual_nav": actual["value"], "actual_observation_id": actual["id"], "actual_first_observed_at": first_seen,
                "research_observed_at": data.get("observed_at"), "resolution_input_hash": digest([base_row, target_row]),
                "basis": "VERIFIED_FORMAL_NAV_AND_RECONSTRUCTED_TOTAL_RETURN"}
            Store.put(c, "forecast_resolution", prediction["id"], resolution, 0)
            count += 1
        return count

    @staticmethod
    def _holding_reference(fund, forecast):
        if (not fund or fund.get("initialization_pending") or
                (fund.get("position_buckets") or {}).get("status") == "BLOCKED" or
                forecast.get("status") != "READY"):
            return {"units": None, "forecast_change": None, "lower_change": None, "upper_change": None, "assumption": HOLDING_ASSUMPTION}
        units = _num(fund["units"])
        capital = units * _num(forecast["base_nav"])
        return {"units": units, "forecast_change": money(capital * _num(forecast["forecast_return"])),
                "lower_change": money(capital * _num(forecast["lower_return"])) if forecast.get("lower_return") is not None else None,
                "upper_change": money(capital * _num(forecast["upper_return"])) if forecast.get("upper_return") is not None else None,
                "assumption": HOLDING_ASSUMPTION, "basis": "CURRENT_CONFIRMED_UNITS_CONSTANT"}

    def _forecast_run_view(self, c, record, basis_hash, snapshot):
        result = _expand(record["result_gzip"])
        result["stale"] = record["basis_hash"] != basis_hash
        funds = {f["id"]: f for f in snapshot["funds"]}
        for fund in result["funds"]:
            for forecast in fund["forecasts"]:
                id = forecast.get("prediction_id")
                # A permanent prediction can predate this cached run. Always show its original generation.
                saved = Store.doc(c, "forecast_prediction", id) if id else None
                if saved:
                    forecast.update({k: v for k, v in saved.items() if k not in {"_version", "model_config"}})
                forecast["resolution"] = Store.doc(c, "forecast_resolution", id) if id else None
                forecast["holdings_reference"] = self._holding_reference(funds.get(fund["fund_id"]), forecast)
        return result

    def forecasting_dashboard(self):
        with self.store.connect() as c:
            c.execute("BEGIN")
            snapshot, _, basis = self._forecast_context(c)
            history = sorted(Store.docs(c, "forecast_run"), key=lambda r: (r["created_at"], r.get("sequence", 0)), reverse=True)
            latest = self._forecast_run_view(c, history[0], digest(basis), snapshot) if history else None
            predictions = Store.docs(c, "forecast_prediction")
            resolutions = {r["id"]: r for r in Store.docs(c, "forecast_resolution")}
            groups = defaultdict(list)
            for prediction in predictions:
                key = (prediction["engine_version"], prediction["horizon"], prediction["fund_id"], prediction["fund_code"])
                groups[key].append(prediction)
                groups[(prediction["engine_version"], prediction["horizon"], None, None)].append(prediction)
            scores = []
            for (engine, horizon, fid, code), values in sorted(groups.items(), key=lambda item: str(item[0])):
                matched = [resolutions[p["id"]] for p in values if p["id"] in resolutions]
                n = len(matched)
                intervals = [r["interval_covered"] for r in matched if r.get("interval_covered") is not None]
                scores.append({"engine_version": engine, "horizon": horizon, "fund_id": fid, "fund_code": code,
                    "scope": "FUND" if fid else "ALL", "count": len(values), "resolved": n, "pending": len(values) - n,
                    "mae_pp": sum((_num(r["absolute_error_pp"]) for r in matched), ZERO) / n if n else None,
                    "baseline_mae_pp": sum((_num(r["baseline_absolute_error_pp"]) for r in matched), ZERO) / n if n else None,
                    "interval_count": len(intervals),
                    "interval_coverage": Decimal(sum(intervals)) / len(intervals) if intervals else None})
            maintenance = Store.doc(c, "forecast_maintenance", "current", {"status": "DISABLED", "message": "首次成功生成后，运行 App 时自动跟踪新净值"})
            return {"version": ENGINE_VERSION, "mode": self.mode, "as_of": snapshot["now"], "business_date": snapshot["business_date"],
                "status": "EMPTY" if not latest else latest["status"], "horizons": list(HORIZONS), "latest": latest,
                "history": [{k: r.get(k) for k in ("id", "created_at", "status", "engine_version", "fund_count", "prediction_count")} for r in history],
                "scores": scores, "maintenance": maintenance,
                "recent_predictions": [dict({k: p.get(k) for k in ("prediction_id", "fund_id", "fund_code", "engine_version", "horizon", "base_date", "target_date", "generated_at", "forecast_return", "lower_return", "upper_return", "predicted_nav_reference")}, resolution=resolutions.get(p["id"]))
                    for p in sorted(predictions, key=lambda p: (p["generated_at"], p["id"]), reverse=True)[:120]],
                "retention": {"run_limit": RUN_LIMIT, "forecasts": "permanent", "resolutions": "immutable"},
                "notice": NOTICE if self.mode == "real" else "演示数据不借用真实历史；本预测实验只为真实账户的六只已核实基金启用。"}

    def forecast_run_export(self, id):
        with self.store.connect() as c:
            c.execute("BEGIN")
            record = Store.doc(c, "forecast_run", id)
            require(record is not None, "FORECAST_NOT_FOUND", "预测分析已被回收或不存在")
            return {"result": _expand(record["result_gzip"]), "inputs": _expand(record["input_gzip"]),
                    "basis": record["basis"], "engine_version": record["engine_version"],
                    "notice": NOTICE, "retention": {"run_limit": RUN_LIMIT}}

    def forecast_maintenance(self):
        """Called outside the App lock. Current-state receipt only; minimum ten-minute cadence."""
        if self.mode != "real":
            return {"status": "DISABLED", "message": "演示账户不自动拟合真实历史"}
        maintenance_lock = self.__dict__.setdefault("_forecast_maintenance_lock", Lock())
        if not maintenance_lock.acquire(blocking=False):
            return {"status": "CHECKING"}
        try:
            with self.store.connect() as c:
                policy = Store.doc(c, "forecast_automation", "current")
                if not policy or not policy.get("enabled"):
                    return {"status": "DISABLED", "message": "首次成功生成后启用"}
                old = Store.doc(c, "forecast_maintenance", "current")
                if old and old.get("next_check_at") and stamp(self.now()) < stamp(old["next_check_at"]):
                    return old
            now = self.now()
            with self.store.transaction() as c:
                old = Store.doc(c, "forecast_maintenance", "current")
                receipt = Store.put(c, "forecast_maintenance", "current", {"status": "CHECKING", "last_check_at": now,
                    "next_check_at": (stamp(now) + timedelta(minutes=10)).isoformat()}, old["_version"] if old else 0)
                snapshot, datasets, basis = self._forecast_context(c)
                resolved = self._resolve_forecasts(c, snapshot, datasets)
                history = sorted(Store.docs(c, "forecast_run"), key=lambda r: (r["created_at"], r.get("sequence", 0)), reverse=True)
                changed = not history or history[0]["basis_hash"] != digest(basis)
                needs_history = any(self._forecast_inputs(f, datasets.get(f.get("fund_code")), now)[0] is None
                                    for f in snapshot["funds"] if f.get("fund_code") in PRODUCTS and f.get("share_class") == "C")
            try:
                if changed:
                    result = self.generate_forecasts({"refresh": needs_history}, automatic=True)
                    status, message = ("READY" if result["status"] == "READY" else "WAIT"), "已按最新正式基准检查未来预测"
                else:
                    status, message = "READY", "预测基准未变化，已检查正式结果，不重复拟合"
                with self.store.transaction() as c:
                    current = Store.doc(c, "forecast_maintenance", "current")
                    return Store.put(c, "forecast_maintenance", "current", {"status": status, "last_check_at": now,
                        "next_check_at": receipt["next_check_at"], "message": message, "resolved_count": resolved}, current["_version"])
            except Exception as exc:
                with self.store.transaction() as c:
                    current = Store.doc(c, "forecast_maintenance", "current")
                    return Store.put(c, "forecast_maintenance", "current", {"status": "WAIT", "last_check_at": now,
                        "next_check_at": (stamp(now) + timedelta(minutes=30)).isoformat(), "message": getattr(exc, "message", "预测维护暂未完成，保留上次结果"),
                        "code": getattr(exc, "code", "FORECAST_MAINTENANCE_ERROR")}, current["_version"])
        finally:
            maintenance_lock.release()
