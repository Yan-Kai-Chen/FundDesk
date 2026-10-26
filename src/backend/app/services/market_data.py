"""Automatic data collection, separate from financial events and UI requests."""
from datetime import timedelta, time
from concurrent.futures import ThreadPoolExecutor

from app.domain.finance import Problem, SHANGHAI, dec, stamp, require
from app.domain.market_calendar import market_session
from app.domain.quote_phase import EQUITY_CLOSE_PROVIDERS, morning_reference
from app.persistence.store import Store, digest
from app.providers.public_nav import SUPPORTED
from app.providers.public_market import fetch_estimate, fetch_official_nav
from app.providers.gold_reference import fetch_gold_reference
from app.services.refresh_policy import AUTO_REFRESH_SECONDS, require_refresh_slot


def _due(at, now):
    return not at or stamp(at) <= stamp(now)


class MarketDataMixin:
    def refresh_intraday(self, fund_id, *, manual=False):
        require(self.mode == "real", "REAL_ONLY", "自动真实行情仅用于真实账户")
        now = self.now()
        # This provider's fund series ends at 15:00 even for HK holdings.
        # Recheck later revisions on the same 10-minute schedule.
        with self.store.transaction() as c:
            fund = self.fund(c, fund_id)
            require(fund.get("fund_code") in SUPPORTED and fund.get("share_class") == "C",
                    "SOURCE_NOT_SUPPORTED", "该基金及份额类别尚未核实自动行情来源")
            gold = fund['fund_code'] == '008987'
            session = market_session(now, 'GOLD_CN' if gold else 'CN')
            if session['status'] not in {'OPEN', 'BREAK', 'CLOSED'}:
                return {'status': 'OUTSIDE_SESSION', 'label': session['label']}
            old = Store.doc(c, "intraday_sync", fund_id)
            history = self.observations(c, fund_id, 'OFFICIAL_NAV')
            date_ = session['date']
            prior = [r for r in history if r['effective_date'] < date_]
            same_basis = bool(old and prior and old.get('reference_date') == prior[-1]['effective_date'] and
                              dec(old.get('reference_nav', '0')) == dec(prior[-1]['value']))
            if not manual and any(r['effective_date'] == date_ for r in history):
                return {'status': 'OFFICIAL_AVAILABLE', 'label': '今日正式净值已披露'}
            if (not manual and same_basis and old.get('latest_date') == date_ and
                    old.get('provider_id') in EQUITY_CLOSE_PROVIDERS and
                    morning_reference(old.get('latest_as_of'), now)):
                return {'status': 'BREAK_CACHED', 'label': '已保存上午最后报价', 'inserted': 0}
            if (not manual and session['status'] == 'CLOSED' and same_basis and old.get('closing_date') == date_ and
                    not _due(old.get('next_attempt_at'), now)):
                return {'status': 'CLOSE_CACHED', 'label': '已保存当日收盘参考', 'inserted': 0}
            require_refresh_slot(old, now, manual=manual)
            token = digest([fund_id, now])[:24]
            started = Store.put(c, "intraday_sync", fund_id,
                dict(old or {}, status="CHECKING", attempt_id=token, attempted_at=now,
                     automatic_interval_seconds=AUTO_REFRESH_SECONDS,
                     next_attempt_at=(stamp(now) + timedelta(seconds=AUTO_REFRESH_SECONDS)).isoformat()), old["_version"] if old else None)
        allow_fallback = manual or _due((old or {}).get("next_fallback_at"), now)
        try:
            # Network is deliberately outside both the SQLite transaction and
            # the application's request lock, so a slow source cannot freeze UI.
            data = (fetch_gold_reference(fund['fund_code'], self.now, history) if gold else
                    fetch_estimate(fund["fund_code"], self.now, allow_fallback=allow_fallback))
            if gold and old and old.get('latest_date') == data['date'] and old.get('reference_mode') == 'TODAY_PM_FIXING':
                require(data['evidence']['reference_mode'] == 'TODAY_PM_FIXING', 'GOLD_FIXING_REGRESSION',
                        '今日午盘基准已保存，本轮来源不完整，保留该基准估算')
            body = data.get("raw_body")
            if body:
                cache = self.root / "data/cache/providers/intraday" / fund["fund_code"]
                cache.mkdir(parents=True, exist_ok=True)
                (cache / (data["raw_sha256"] + ".txt")).write_bytes(body)
            with self.store.transaction() as c:
                current = Store.doc(c, "intraday_sync", fund_id)
                require(current.get("attempt_id") == token, "COLLECTION_SUPERSEDED", "本轮采集已被更新任务替代")
                prior = [r for r in self.observations(c, fund_id, "OFFICIAL_NAV") if r["effective_date"] < data["date"]]
                require(prior and prior[-1]["effective_date"] == data["reference_date"] and
                        dec(prior[-1]["value"]) == data["reference_nav"],
                        "QUOTE_BASIS_MISMATCH", "盘中来源基准与已保存正式净值不符，先核对正式净值")
                evidence = {"provider_id": data["provider_id"], "source_url": data["source_url"],
                            "reference_date": data["reference_date"], "reference_nav": data["reference_nav"], **data.get('evidence', {})}
                inserted = 0
                for row in data["records"]:
                    row_evidence = evidence
                    if data.get('closing_reference') and stamp(row['as_of']).astimezone(SHANGHAI).time() == time(15):
                        row_evidence = dict(evidence, quote_phase='SOURCE_CLOSE')
                    result = self.observe({"fund_id": fund_id, "value_kind": data.get('value_kind', 'THIRD_PARTY_ESTIMATE'),
                        "value": row["value"], "effective_date": row["effective_date"], "as_of": row["as_of"],
                        "source": data["source"], "quality": data.get('quality', 'PUBLIC_THIRD_PARTY_ESTIMATE')}, c,
                        provider_evidence=dict(row_evidence, raw_response_hash=data["raw_sha256"]))
                    inserted += not result["duplicate"]
                saved = dict(current, status="READY", last_success_at=self.now(), failure_count=0,
                             provider_id=data["provider_id"], source=data["source"], source_url=data["source_url"],
                             raw_sha256=data["raw_sha256"], latest_date=data["date"],
                             reference_date=data['reference_date'], reference_nav=str(data['reference_nav']),
                             latest_as_of=data["records"][-1]["as_of"], count=len(data["records"]),
                             fallback_errors=data.get("attempts", []))
                if gold:
                    saved['reference_mode'] = data['evidence']['reference_mode']
                if data.get('closing_reference') or (gold and data['evidence']['reference_mode'] in {'DAY_CLOSE_PROXY', 'TODAY_PM_FIXING'}):
                    saved['closing_date'] = data['date']
                for k in ("error_code", "error_message"):
                    saved.pop(k, None)
                if data.get("fallback_checked"):
                    saved["next_fallback_at"] = (stamp(self.now()) + timedelta(hours=6)).isoformat()
                Store.put(c, "intraday_sync", fund_id, saved, current["_version"])
                self.capture_estimate_results(c,fund_id)
                return {"status": "READY", "inserted": inserted, "as_of": saved["latest_as_of"], "source": data["source"]}
        except Exception as exc:
            with self.store.transaction() as c:
                current = Store.doc(c, "intraday_sync", fund_id)
                if current.get("attempt_id") == token:
                    failures = (old or {}).get("failure_count", 0) + 1
                    delay = min(900, AUTO_REFRESH_SECONDS * 2 ** min(failures - 1, 4))
                    saved = dict(current, status="FAILED", failure_count=failures,
                                 error_code=getattr(exc, "code", "SOURCE_UNAVAILABLE"),
                                 error_message=str(exc)[:180] if isinstance(exc, Problem) else "行情采集失败，保留上次数据",
                                 next_attempt_at=(stamp(self.now()) + timedelta(seconds=delay)).isoformat())
                    if getattr(exc, "fallback_checked", False):
                        saved["next_fallback_at"] = (stamp(self.now()) + timedelta(hours=6)).isoformat()
                    if getattr(exc, "provider_errors", None):
                        saved["fallback_errors"] = exc.provider_errors
                    Store.put(c, "intraday_sync", fund_id, saved, current["_version"])
            raise

    def refresh_market(self, fund_id, *, manual=False):
        require(self.mode == "real", "REAL_ONLY", "自动真实行情仅用于真实账户")
        with self.store.connect() as c:
            fund = self.fund(c, fund_id)
            require(fund.get("fund_code") in SUPPORTED and fund.get("share_class") == "C",
                    "SOURCE_NOT_SUPPORTED", "该基金及份额类别尚未核实自动行情来源")
            nav_sync = Store.doc(c, "nav_sync", fund_id)
        now = stamp(self.now())
        local = now.astimezone(SHANGHAI)
        # Keep checking during the disclosure window, including same-day NAV
        # revisions after the first disclosure. Repeated values deduplicate.
        last = stamp(nav_sync["attempted_at"]) if nav_sync and nav_sync.get("attempted_at") else None
        same_date = bool(last and last.astimezone(SHANGHAI).date() == local.date())
        nav_due = manual or not same_date or (local.time() >= time(16) and
                                             (not last or (now - last).total_seconds() >= AUTO_REFRESH_SECONDS))
        result = {"fund_id": fund_id, "nav": {"status": "CACHED"}, "intraday": {"status": "CACHED"}}
        # Ensure the last official reference exists before importing estimates.
        if nav_due:
            try:
                nav = self.refresh_public_nav(fund_id, fetcher=fetch_official_nav, manual=manual)
                result["nav"] = {"status": "READY", "latest_date": nav["latest_date"], "source": nav["source"]}
            except Problem as exc:
                result["nav"] = {"status": "WAIT" if exc.code in {"RATE_LIMITED", "COLLECTION_IN_PROGRESS"} else "FAILED", "code": exc.code}
        try:
            result["intraday"] = self.refresh_intraday(fund_id, manual=manual)
        except Problem as exc:
            result["intraday"] = {"status": "WAIT" if exc.code in {"RATE_LIMITED", "COLLECTION_IN_PROGRESS"} else "FAILED",
                                   "code": exc.code, "message": exc.message}
        return result

    def collect_market_due(self):
        if self.mode != "real":
            return []
        with self.store.connect() as c:
            funds = [f for f in Store.docs(c, "fund") if f.get("watching") and
                     f.get("fund_code") in SUPPORTED and f.get("share_class") == "C"]
        return [self.refresh_market(f["id"]) for f in funds]

    def refresh_all_market(self):
        """Explicit user refresh; each product has independent source results."""
        require(self.mode == "real", "REAL_ONLY", "自动真实行情仅用于真实账户")
        with self.store.connect() as c:
            funds = Store.docs(c, "fund")

        def refresh(fund):
            row = {"fund_id": fund["id"], "fund_code": fund.get("fund_code")}
            if fund.get("fund_code") not in SUPPORTED or fund.get("share_class") != "C":
                return dict(row, status="UNSUPPORTED", code="SOURCE_NOT_SUPPORTED",
                            message="该基金及份额类别尚未核实自动行情来源")
            try:
                result = self.refresh_market(fund["id"], manual=True)
                phases = [result.get(k, {}).get("status") for k in ("nav", "intraday")]
                status = "FAILED" if "FAILED" in phases else "WAIT" if "WAIT" in phases else "COMPLETED"
                return dict(row, **result, status=status)
            except Problem as exc:
                return dict(row, status="WAIT" if exc.code in {"RATE_LIMITED", "COLLECTION_IN_PROGRESS", "COLLECTION_SUPERSEDED"} else "FAILED",
                            code=exc.code, message=exc.message)
            except Exception:
                return dict(row, status="FAILED", code="SOURCE_UNAVAILABLE", message="本轮获取失败，保留已保存数据")

        # Never hold the application lock or an SQLite transaction over network calls.
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(refresh, funds))
        counts = {name + "_count": sum(r["status"] == status for r in results)
                  for name, status in [("completed", "COMPLETED"), ("waiting", "WAIT"),
                                       ("failed", "FAILED"), ("unsupported", "UNSUPPORTED")]}
        return {"status": "EMPTY" if not results else "COMPLETED" if counts["completed_count"] == len(results) else "PARTIAL",
                "total_count": len(results), **counts, "results": results}
