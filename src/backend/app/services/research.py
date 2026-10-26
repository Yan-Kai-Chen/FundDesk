"""Explicit research runs and evidence, separate from the financial event stream."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
import base64
import copy
import gzip
import json

from app.domain.finance import Problem, ZERO, SHANGHAI, dec, money, require, stamp
from app.domain.method_observations import LABELS, observe_methods, method_targets
from app.domain.research_calendar import reference_sessions
from app.persistence.store import Store, digest, pack
from app.providers.research_history import PRODUCTS, CASH_CODE, fetch_body, parse_history, parse_cash
from app.research_methods.engine.api import evaluate as evaluate_methods
from app.research_methods.engine.domain import InputError
from app.research_methods.engine.contracts import validate_input

ASSETS = Path(__file__).resolve().parents[1] / "research_methods"
LEVELS = {
    "OBSERVE": {"label": "观察", "volatility_limit": None, "band": None},
    "CAUTIOUS": {"label": "谨慎", "volatility_limit": "0.10", "band": "0.05"},
    "BALANCED": {"label": "均衡", "volatility_limit": "0.16", "band": "0.04"},
    "ACTIVE": {"label": "积极", "volatility_limit": "0.24", "band": "0.03"},
}
RUN_LIMIT = 30


def _compressed(value):
    return base64.b64encode(gzip.compress(pack(value).encode(), mtime=0)).decode()


def _expanded(value):
    return json.loads(gzip.decompress(base64.b64decode(value)))


def _config():
    return json.loads((ASSETS / "config/methods.json").read_text())


class ResearchMixin:
    def research_policy(self, c):
        return Store.doc(c, "research_policy", "current", {"level": "OBSERVE", "target_weights": {},
            "cash_budget": None, "scope": "REGISTERED_FUNDS_ONLY", "_version": 0})

    def save_research_policy(self, p):
        require(set(p) <= {"level", "target_weights", "target_percentages", "cash_budget", "expected_version"}, "INVALID_POLICY", "研究设置包含未知字段")
        require(not ("target_weights" in p and "target_percentages" in p), "INVALID_POLICY", "目标配比只能采用一种输入单位")
        require(p.get("level") in LEVELS, "INVALID_POLICY", "请选择研究强度")
        targets = p.get("target_percentages", p.get("target_weights", {}))
        require(isinstance(targets, dict), "INVALID_POLICY", "请提供基金目标配比")
        values = {k: dec(v) / (100 if "target_percentages" in p else 1) for k, v in targets.items()}
        require(all(v <= 1 for v in values.values()) and sum(values.values(), ZERO) <= 1,
                "INVALID_POLICY", "目标配比合计不能超过100%")
        budget = p.get("cash_budget")
        if budget is not None: budget = str(money(dec(budget)))
        with self.store.transaction() as c:
            ids = {f["id"] for f in Store.docs(c, "fund")}
            require(set(targets) <= ids, "INVALID_POLICY", "目标包含不存在的基金")
            return Store.put(c, "research_policy", "current", {"level": p["level"],
                "target_weights": {k: str(v) for k, v in values.items()}, "cash_budget": budget,
                "scope": "REGISTERED_FUNDS_PLUS_EXPLICIT_RESEARCH_BUDGET", "known_at": self.now(),
                "authority": "USER_CONFIGURED", "preset_version": "research-observation-levels-v1"}, p.get("expected_version"))

    def research_datasets(self, c):
        return {d["id"]: d["data"] for d in Store.docs(c, "research_series")}

    def research_basis(self, snapshot, datasets, policy, imported=None):
        return {"revision": snapshot["revision"], "business_date": snapshot["business_date"],
                "funds": [{"id": f["id"], "version": f["_version"], "nav": f["nav"]["id"] if f["nav"] else None,
                           "quote": (f.get("estimate") or {}).get("id"), "rules": f["rules"]["_version"]} for f in snapshot["funds"]],
                "datasets": {key: digest(value["rows"]) for key, value in datasets.items()},
                "policy_version": policy["_version"], "import_version": (imported or {}).get("_version", 0)}

    def research_dashboard(self):
        with self.store.connect() as c:
            c.execute("BEGIN")
            snapshot, policy = self.snapshot(c), self.research_policy(c)
            datasets = self.research_datasets(c)
            imported = Store.doc(c, "research_input", "current")
            basis = self.research_basis(snapshot, datasets, policy, imported)
            history = sorted(Store.docs(c, "research_run"), key=lambda r: (r["created_at"], r.get("sequence", 0)), reverse=True)
            latest = self.research_run_view(history[0], basis, snapshot["now"]) if history else None
            readiness = []
            for f in snapshot["funds"]:
                data = datasets.get(f.get("fund_code"))
                if f.get("share_class") != "C": data = None
                kind, scope, _ = PRODUCTS.get(f.get("fund_code"), ("UNKNOWN", "UNKNOWN", "UNKNOWN"))
                if f.get("share_class") != "C": kind, scope = "UNKNOWN", "UNKNOWN"
                readiness.append({"fund_id": f["id"], "fund_code": f.get("fund_code"), "kind": kind, "scope": scope,
                    "history_count": len(data["rows"]) if data else 0, "history_end": data["rows"][-1][0] if data else None,
                    "history_start": data["rows"][0][0] if data else None, "source": data.get("source") if data else None,
                    "observed_at": data.get("observed_at") if data else None,
                    "source_url": data.get("source_url") if data else None})
            forecasts = [dict(json.loads(r["payload"]), resolution=json.loads(r["resolution"]) if r["resolution"] else None)
                for r in c.execute("SELECT p.payload,r.payload AS resolution FROM predictions p LEFT JOIN resolutions r ON p.id=r.prediction_id")]
            scores = {}
            for fund in snapshot["funds"]:
                saved = [p for p in forecasts if p["fund_id"] == fund["id"]]
                matched = [p for p in saved if p["resolution"]]
                scores[fund["id"]] = {"count": len(saved), "resolved": len(matched), "pending": len(saved) - len(matched),
                    "mae": sum((abs(dec(p["resolution"]["error"], nonnegative=False)) for p in matched), ZERO) / len(matched) if matched else None,
                    "mape": sum((Decimal(p["resolution"]["absolute_percentage_error"]) for p in matched), ZERO) / len(matched) if matched else None}
            return {"version": "research-workbench-v1", "mode": self.mode, "methods": LABELS, "levels": LEVELS,
                "policy": policy, "data": readiness, "cash_benchmark_ready": bool(datasets.get("cash")),
                "current_basis_hash": digest(basis), "forecast_scores": scores,
                "sync": Store.doc(c, "research_sync", "current"), "latest": latest,
                "history": [{k: r[k] for k in ["id", "created_at", "selected_methods", "data_basis"]} for r in history[:RUN_LIMIT]],
                "snapshot_import": {"version": imported["_version"], "as_of": imported["as_of"], "raw_hash": imported["raw_hash"]} if imported else None,
                "retention": {"analysis_limit": RUN_LIMIT, "forecasts": "permanent", "series": "latest_compact_version"}}

    @staticmethod
    def research_run_view(record, basis, now=None):
        result = _expanded(record["result_gzip"])
        result["basis_hash"] = record["basis_hash"]
        result["stale"] = record["basis_hash"] != digest(basis)
        engine = result.get("engine_result")
        if now and engine:
            result["stale"] = result["stale"] or stamp(engine["valid_until"]) <= stamp(now)
            for quote in engine.get("intraday_scenario", {}).get("quotes", {}).values():
                if (stamp(now) - stamp(quote["as_of"])).total_seconds() > 900:
                    result["stale"] = True
        return result

    def refresh_research_data(self):
        require(self.mode == "real", "REAL_ONLY", "演示账户使用独立合成研究样本")
        now = self.now()
        with self.store.transaction() as c:
            old = Store.doc(c, "research_sync", "current")
            require(not old or old.get("status") != "CHECKING" or stamp(now) - stamp(old["started_at"]) > timedelta(minutes=2),
                    "COLLECTION_IN_PROGRESS", "研究资料正在更新")
            token = digest([now, "research"])
            Store.put(c, "research_sync", "current", {"status": "CHECKING", "started_at": now, "token": token}, old["_version"] if old else 0)
            codes = {f["fund_code"] for f in Store.docs(c, "fund") if f.get("fund_code") in PRODUCTS and f.get("share_class") == "C"}
        def collect(code):
            body = fetch_body(code)
            return parse_cash(body, self.now()) if code == CASH_CODE else parse_history(body, code, self.now())
        outcomes = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(collect, code): code for code in codes | {CASH_CODE}}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    data = future.result()
                    key = "cash" if code == CASH_CODE else code
                    with self.store.transaction() as c:
                        old = Store.doc(c, "research_series", key)
                        if not old or digest(old["data"]["rows"]) != digest(data["rows"]):
                            Store.put(c, "research_series", key, {"id": key, "data": data}, old["_version"] if old else 0)
                    outcomes.append({"fund_code": code, "status": "READY", "rows": len(data["rows"]), "latest_date": data["rows"][-1][0]})
                except Problem as exc:
                    outcomes.append({"fund_code": code, "status": "WAIT", "code": exc.code, "message": exc.message})
                except Exception as exc:
                    outcomes.append({"fund_code": code, "status": "WAIT", "code": "RESEARCH_SOURCE_ERROR", "message": "研究来源格式变化，保留旧资料"})
        with self.store.transaction() as c:
            current = Store.doc(c, "research_sync", "current")
            require(current["token"] == token, "COLLECTION_SUPERSEDED", "本次资料更新已被替代")
            return Store.put(c, "research_sync", "current", {"status": "READY" if all(r["status"] == "READY" for r in outcomes) else "PARTIAL",
                "started_at": now, "finished_at": self.now(), "outcomes": sorted(outcomes, key=lambda r: r["fund_code"])}, current["_version"])

    def validate_research_input(self, raw, methods=None):
        require(isinstance(raw, dict), "INVALID_RESEARCH_INPUT", "请提供研究快照对象")
        try:
            validate_input(raw)
        except InputError as exc:
            raise Problem("INVALID_RESEARCH_INPUT", "研究快照格式不完整：" + str(exc)[:200])
        require(raw.get("mode") == ("SYNTHETIC" if self.mode == "demo" else "RESEARCH"),
                "MODE_MISMATCH", "合成研究快照只能用于演示模式")
        if self.mode == "real":
            require(stamp(raw.get("as_of")) <= stamp(self.now()), "INVALID_TIME", "研究快照不能来自未来")
            with self.store.connect() as c:
                c.execute("BEGIN")
                current = self.snapshot(c)
            by_id = {f["id"]: f for f in current["funds"]}
            require({a.get("id") for a in raw.get("instruments", [])} == set(by_id), "RESEARCH_SCOPE_MISMATCH", "研究快照须覆盖全部已登记基金，不能丢弃不适用持仓")
            for asset in raw["instruments"]:
                f = by_id[asset["id"]]
                known = PRODUCTS.get(f.get("fund_code")) if f.get("share_class") == "C" else None
                require(known and (asset.get("kind"), asset.get("market_scope"), asset.get("exposure_id")) == known,
                        "RESEARCH_IDENTITY_MISMATCH", "研究产品分类、市场或暴露与已核实映射不符")
            for holding in raw.get("portfolio", {}).get("holdings", []):
                f = by_id.get(holding.get("asset_id"))
                require(f is not None, "RESEARCH_SCOPE_MISMATCH", "研究持仓基金不在本地记录中")
                require(sum((dec(l["units"]) for l in holding["lots"]), ZERO) == dec(f["units"]), "RESEARCH_HOLDING_MISMATCH", "研究份额与当前账本不符")
                base = f["position_buckets"].get("groups", {}).get("BASE", {}).get("units")
                require(base is not None and dec(holding["protected_units"]) >= dec(base), "RESEARCH_BASE_PROTECTION", "研究保护份额不能小于现有底仓")
                require(f.get("nav") and holding["mark"]["date"] == f["nav"]["effective_date"] and dec(holding["mark"]["value"]) == dec(f["nav"]["value"]),
                        "RESEARCH_NAV_MISMATCH", "研究标记净值与当前正式净值不符")
        try:
            return evaluate_methods(raw, _config(), methods)
        except (InputError, KeyError, TypeError, ArithmeticError) as exc:
            raise Problem("INVALID_RESEARCH_INPUT", "研究快照不满足合同：" + str(exc)[:200])

    def import_research_input(self, p, preview=False):
        raw = p.get("snapshot")
        result = self.validate_research_input(raw)
        raw_hash = digest(raw)
        if preview:
            return {"raw_hash": raw_hash, "engine_version": result["engine_version"],
                    "as_of": result["as_of"], "methods": {k: r["analysis_status"] for k, r in result["methods"].items()}, "execution_enabled": False}
        require(p.get("preview_hash") == raw_hash, "PREVIEW_REQUIRED", "请先核对同一份研究快照")
        with self.store.transaction() as c:
            return Store.put(c, "research_input", "current", {"raw_hash": raw_hash, "as_of": raw["as_of"],
                "raw_gzip": _compressed(raw), "imported_at": self.now()}, p.get("expected_version"))

    def generate_research(self, p):
        methods = p.get("methods", list(LABELS))
        require(isinstance(methods, list) and bool(methods) and all(isinstance(m, str) for m in methods) and
                len(set(methods)) == len(methods) and set(methods) <= set(LABELS), "INVALID_METHOD", "请选择趋势、估值或再平衡方法")
        request_key = p.get("key")
        require(isinstance(request_key, str) and 8 <= len(request_key) <= 160, "IDEMPOTENCY_REQUIRED", "本次分析需要独立请求标识")
        id = "research_" + digest([self.mode, request_key])[:24]
        request_hash = digest({"methods": methods, "key": request_key})
        with self.store.connect() as c:
            c.execute("BEGIN")
            snapshot, policy = self.snapshot(c), self.research_policy(c)
            # The final commit below rechecks this basis after computation.
            datasets = self.research_datasets(c)
            imported = Store.doc(c, "research_input", "current")
            basis = self.research_basis(snapshot, datasets, policy, imported)
            old = Store.doc(c, "research_run", id)
            if old:
                require(old["request_hash"] == request_hash, "IDEMPOTENCY_CONFLICT", "同一请求标识不能更换方法")
                return self.research_run_view(old, basis, snapshot["now"])
        recognized = any(f.get("fund_code") in PRODUCTS and f.get("share_class") == "C" for f in snapshot["funds"])
        needs_data = self.mode == "real" and recognized and not imported and (not datasets.get("cash") or any(
            f.get("fund_code") in PRODUCTS and f.get("share_class") == "C" and
            (not datasets.get(f["fund_code"]) or f.get("nav") and (
                datasets[f["fund_code"]]["rows"][-1][0] != f["nav"]["effective_date"] or
                dec(datasets[f["fund_code"]]["rows"][-1][1]) != dec(f["nav"]["value"]))) for f in snapshot["funds"]))
        if needs_data:
            self.refresh_research_data()
            with self.store.connect() as c:
                c.execute("BEGIN")
                snapshot, policy = self.snapshot(c), self.research_policy(c)
                datasets = self.research_datasets(c)
                imported = Store.doc(c, "research_input", "current")
                basis = self.research_basis(snapshot, datasets, policy, imported)
        observations = observe_methods(snapshot, datasets, policy, methods)
        evidence = None
        if imported:
            try:
                candidate_input = _expanded(imported["raw_gzip"])
                self.validate_research_input(candidate_input, methods)
                evidence = candidate_input
            except Problem:
                pass
        targets = method_targets(snapshot, datasets, policy, methods, _config(), LEVELS[policy["level"]], evidence)
        engine_result, engine_reasons, raw = None, [], None
        if self.mode == "demo":
            raw = json.loads(gzip.decompress((ASSETS / "examples/synthetic_snapshot.json.gz").read_bytes()))
            engine_result = evaluate_methods(raw, _config(), methods)
        elif imported and policy["level"] != "OBSERVE" and policy.get("cash_budget") is not None and set(policy["target_weights"]) == {f["id"] for f in snapshot["funds"]}:
            raw = _expanded(imported["raw_gzip"])
            # The evidence comes from the import; targets/budget come only from
            # the user's explicit App research settings, never from demo policy.
            raw["as_of"] = snapshot["now"]
            policy_ref = "APP_EXPLICIT_RESEARCH_POLICY"
            raw["sources"] = [s for s in raw["sources"] if s["id"] != policy_ref] + [{"id": policy_ref, "kind": "VERIFIED", "description": "用户保存的研究目标和独立预算，不是账户总资金", "url": None}]
            raw["policy"].update(source_ref=policy_ref, known_at=policy["known_at"],
                target_weights=policy["target_weights"], volatility_limit=LEVELS[policy["level"]]["volatility_limit"], band=LEVELS[policy["level"]]["band"])
            raw["portfolio"].update(source_ref=policy_ref, known_at=snapshot["now"], cash_settled=policy["cash_budget"])
            for holding in raw["portfolio"]["holdings"]:
                holding["pending_application"] = any(o["fund_id"] == holding["asset_id"] and not o["closed"] for o in snapshot["orders"])
            try:
                engine_result = self.validate_research_input(raw, methods)
                require(stamp(engine_result["valid_until"]) > stamp(self.now()), "RESEARCH_INPUT_EXPIRED", "研究输入已过参考截止时间，请更新快照")
            except Problem as exc:
                engine_result = None; engine_reasons.append({"code": exc.code, "message": exc.message})
        else:
            engine_reasons = [{"code": "COMPLETE_RESEARCH_SNAPSHOT_REQUIRED", "message": "完整金额方案还需非观察档位、全部目标配比、显式研究预算及核实的申赎日历与批次；观察结果可直接查看。"}]
        if policy["level"] == "OBSERVE" and self.mode == "real" and engine_result is not None:
            engine_reasons.append({"code": "OBSERVE_ONLY", "message": "当前为观察模式，金额方案只展示为研究假设，不启用交易。"})
        now = self.now()
        result = {"id": id, "created_at": now, "selected_methods": methods, "mode": self.mode,
                  "execution_enabled": False, "observations": observations, "target_result": targets, "engine_result": engine_result,
                  "engine_reasons": engine_reasons, "data_basis": "SYNTHETIC_FUNCTIONAL_ONLY" if self.mode == "demo" else "PUBLIC_HISTORY_RECONSTRUCTION",
                  "engine_version": "0.1.0", "candidate_version": _config()["version"],
                  "adapter_version": "funddesk-research-v1",
                  "method_config_hash": digest(_config()), "raw_input_hash": digest(raw) if raw else None,
                  "import_source_hash": imported["raw_hash"] if imported else None,
                  "forecasts": [], "stale": False}
        with self.store.transaction() as c:
            duplicate = Store.doc(c, "research_run", id)
            if duplicate:
                require(duplicate["request_hash"] == request_hash, "IDEMPOTENCY_CONFLICT", "同一请求标识不能更换方法")
                return self.research_run_view(duplicate, basis, self.now())
            current_snapshot, current_policy = self.snapshot(c), self.research_policy(c)
            current_basis = self.research_basis(current_snapshot, self.research_datasets(c), current_policy, Store.doc(c, "research_input", "current"))
            require(digest(current_basis) == digest(basis), "SNAPSHOT_CHANGED", "资料或持仓刚刚更新，请重新生成以保持同一快照")
            for f in snapshot["funds"]:
                target = next((d for d in reference_sessions() if d >= snapshot["business_date"] and
                    f.get("nav") and d > f["nav"]["effective_date"]), None)
                if not target: continue
                try:
                    forecast = self.predict({"fund_id": f["id"], "key": "research-forecast:" + digest([f["id"], f["nav"]["id"], target]),
                        "target_date": target, "method": "LAST_VALUE"}, c=c)
                    result["forecasts"].append({"fund_id": f["id"], "status": "READY", "prediction_id": forecast["id"],
                        "target_date": target, "predicted_nav": forecast["predicted_nav"], "method": "LAST_VALUE"})
                except Problem as exc:
                    result["forecasts"].append({"fund_id": f["id"], "status": "WAIT", "code": exc.code, "message": exc.message})
            sequence = max((r.get("sequence", 0) for r in Store.docs(c, "research_run")), default=0) + 1
            record = Store.put(c, "research_run", id, {"id": id, "created_at": now, "sequence": sequence, "basis_hash": digest(basis),
                "basis": basis, "request_hash": request_hash, "selected_methods": methods, "data_basis": result["data_basis"],
                "result_gzip": _compressed(result), "input_gzip": _compressed(raw) if raw else None,
                "context_gzip": _compressed({"snapshot": {"now": snapshot["now"], "business_date": snapshot["business_date"],
                    "funds": [dict({key: f[key] for key in ["id", "fund_code", "share_class", "units", "nav", "market_value"]},
                        position_buckets={"groups": {name: {"units": group["units"]} for name, group in f["position_buckets"].get("groups", {}).items()}}) for f in snapshot["funds"]]},
                    "datasets": datasets, "policy": policy, "methods": methods})}, 0)
            # Research runs are bounded cached analyses, not financial facts or forecast outcomes.
            old_runs = sorted(Store.docs(c, "research_run"), key=lambda r: (r["created_at"], r.get("sequence", 0)), reverse=True)
            for old_run in old_runs[RUN_LIMIT:]:
                c.execute("DELETE FROM documents WHERE kind='research_run' AND id=?", (old_run["id"],))
            return self.research_run_view(record, basis, now)

    def research_run_export(self, id):
        with self.store.connect() as c:
            record = Store.doc(c, "research_run", id)
            require(record is not None, "RESEARCH_NOT_FOUND", "研究记录已被回收或不存在")
            return {"result": _expanded(record["result_gzip"]), "input": _expanded(record["input_gzip"]) if record.get("input_gzip") else None,
                    "input_context": _expanded(record["context_gzip"]) if record.get("context_gzip") else None,
                    "basis": record["basis"], "method_config": _config()}

    def research_get_run(self, id):
        with self.store.connect() as c:
            c.execute("BEGIN")
            record = Store.doc(c, "research_run", id)
            require(record is not None, "RESEARCH_NOT_FOUND", "研究记录已被回收或不存在")
            snapshot = self.snapshot(c)
            basis = self.research_basis(snapshot, self.research_datasets(c), self.research_policy(c), Store.doc(c, "research_input", "current"))
            return self.research_run_view(record, basis, snapshot["now"])

    @staticmethod
    def research_input_schema():
        return json.loads((ASSETS / "contracts/input.schema.json").read_text())
