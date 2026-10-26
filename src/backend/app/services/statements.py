"""Screenshot evidence is versioned separately from confirmed ledger events."""
from datetime import datetime, timedelta
import re

from app.domain.finance import ZERO, Problem, amount, dec, day, stamp, require, SHANGHAI
from app.persistence.store import Store, digest
from app.providers.public_nav import fetch_public_nav, SUPPORTED
from app.services.t_attribution import reconstruct, attribute
from app.domain.buckets import reference_buckets
from app.services.refresh_policy import AUTO_REFRESH_SECONDS, require_refresh_slot
from app.schemas.asset_import import transaction_metadata


class StatementMixin:
    def save_statement(self, p, c=None):
        if c is None:
            with self.store.transaction() as connection:
                return self.save_statement(p, connection)
        fund = self.fund(c, p["fund_id"])
        require(p.get("channel") == fund["channel"], "CHANNEL_MISMATCH", "交易资料渠道与基金档案不一致")
        sources = p.get("sources", [])
        require(isinstance(sources, list) and 1 <= len(sources) <= 100, "EVIDENCE_REQUIRED", "请保留交易资料来源")
        source_map = {}
        for source in sources:
            sha = source.get("sha256", "")
            require(bool(re.fullmatch(r"[0-9a-f]{64}", sha)), "EVIDENCE_HASH_INVALID", "来源需要有效 SHA256")
            name = str(source.get("filename", ""))
            require(bool(name) and len(name) <= 150 and "/" not in name and "\\" not in name,
                    "EVIDENCE_NAME_INVALID", "来源文件只保存文件名")
            require(sha not in source_map, "EVIDENCE_DUPLICATE", "来源文件重复")
            source_map[sha] = {"sha256": sha, "filename": name, "label": str(source.get("label", name))[:80]}
        rows = p.get("rows", [])
        require(isinstance(rows, list) and 1 <= len(rows) <= 1000, "STATEMENT_ROWS_INVALID", "交易资料应有 1–1000 行")
        unique, occurrence_count = {}, 0
        for r in rows:
            if r.get("status") == "CLOSED":
                continue
            require(r.get("side") in {"BUY", "SELL"} and r.get("unit") in {"CNY", "FUND_UNIT"}, "STATEMENT_ROW_INVALID", "交易方向或单位无效")
            require(r["side"] != "BUY" or r["unit"] == "CNY", "STATEMENT_ROW_INVALID", "本资料入口的买入按人民币金额登记")
            require(r.get("status") in {"SHOWN", "PENDING_SETTLEMENT"}, "STATEMENT_ROW_INVALID", "交易资料状态无效")
            require(r["status"] != "PENDING_SETTLEMENT" or r["side"] == "SELL", "STATEMENT_ROW_INVALID", "预计到账适用于卖出资料")
            at = r["displayed_at"]
            require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", at)), "INVALID_TIME", "截图时间须保留完整年月日时分秒")
            local = datetime.fromisoformat(at).replace(tzinfo=SHANGHAI)
            require(local <= stamp(self.now()), "FUTURE_EVENT", "截图交易时间晚于当前时间")
            value = amount(r["value"], positive=True) if r["unit"] == "CNY" else dec(r["value"], positive=True)
            try:
                metadata = transaction_metadata(r)
            except ValueError as exc:
                raise Problem('STATEMENT_METADATA_INVALID', str(exc))
            refs = r.get("source_refs", [])
            require(bool(refs), "EVIDENCE_REQUIRED", "每笔记录必须有截图行号来源")
            clean_refs = []
            for ref in refs:
                require(ref.get("sha256") in source_map and type(ref.get("row")) is int and 1 <= ref["row"] <= 1000,
                        "EVIDENCE_REQUIRED", "截图来源或行号无效")
                clean_refs.append({"sha256": ref["sha256"], "row": ref["row"]})
            key = digest([fund["id"], at, r["side"], r["unit"], format(value.normalize(), "f")])
            clean = {"id": "statement_row_" + key[:20], "displayed_at": at,
                     "side": r["side"], "unit": r["unit"], "value": value, "status": r["status"],
                     "status_text": str(r.get("status_text", ""))[:120], "source_refs": clean_refs,
                     "expected_settlement_date": r.get("expected_settlement_date") or None, **metadata}
            if clean["expected_settlement_date"]:
                require(day(clean["expected_settlement_date"]) >= local.date(), "INVALID_DATE", "预计到账日期早于申请日期")
            if key in unique:
                old = unique[key]
                require((old["status"], old["status_text"], old["expected_settlement_date"]) ==
                        (clean["status"], clean["status_text"], clean["expected_settlement_date"]) and
                        transaction_metadata(old) == metadata,
                        "STATEMENT_CONFLICT", "重复交易的状态不一致，请核对后再导入")
                old["source_refs"].extend(clean_refs)
            else:
                unique[key] = clean
            occurrence_count += len(clean_refs)
        require(bool(unique), "STATEMENT_ROWS_INVALID", "没有可登记的有效交易")
        clean = {"fund_id": fund["id"], "channel": fund["channel"], "sources": list(source_map.values()),
                 "rows": sorted(unique.values(), key=lambda r: r["displayed_at"]),
                 "history_complete": p.get("history_complete") is True,
                 "history_completeness_basis": str(p.get("history_completeness_basis", ""))[:300],
                 "timezone": "Asia/Shanghai", "timezone_basis": "按支付宝人民币基金页面北京时间解释，待渠道详情核对",
                 "reconciliation_status": "AWAITING_CONFIRMATIONS", "occurrence_count": occurrence_count}
        old = Store.doc(c, "statement", fund["id"])
        if old and digest({k:v for k,v in old.items() if k not in {"_version", "recorded_at"}}) == digest(clean):
            return dict(old, duplicate=True)
        clean["recorded_at"] = self.now()
        saved = Store.put(c, "statement", fund["id"], clean, p.get("expected_version"))
        Store.put(c, "statement_history", fund["id"] + ":" + str(saved["_version"]), saved)
        return saved

    def statement_summary(self, c, fund_id, history):
        statement = Store.doc(c, "statement", fund_id)
        if not statement:
            return None
        tracking_started = fund_id in self.state(c)['tracking']
        snapshot_started = Store.doc(c,'migration_opening',fund_id) is not None
        navs = {x["effective_date"]: x for x in history}
        changes = {x["effective_date"]: dec(x["value"]) / dec(history[i-1]["value"]) - 1
                   for i,x in enumerate(history) if i}
        rows = [dict(r, same_date_nav=navs.get(r["displayed_at"][:10]),
                     same_date_change=changes.get(r["displayed_at"][:10]), ledger_posted=False) for r in statement["rows"]]
        active = rows
        first = next((r["displayed_at"][:10] for r in active), None)
        first_nav = navs.get(first)
        return dict(statement, rows=rows, tracking_started=tracking_started, t_analysis=self.t_analysis(c, fund_id, history),
                    bucket_analysis=self.bucket_analysis(c,fund_id,history), summary={
            "count": len(rows), "occurrence_count": statement["occurrence_count"],
            "duplicate_count": statement["occurrence_count"]-len(rows),
            "buy_count": sum(r["side"] == "BUY" for r in active),
            "buy_displayed_total": sum((dec(r["value"]) for r in active if r["side"] == "BUY" and r["unit"] == "CNY"), ZERO),
            "sell_displayed_amount": sum((dec(r["value"]) for r in active if r["side"] == "SELL" and r["unit"] == "CNY"), ZERO),
            "pending_sell_units": sum((dec(r["value"]) for r in active if r["side"] == "SELL" and r["unit"] == "FUND_UNIT" and r["status"] == "PENDING_SETTLEMENT"), ZERO),
            "first_transaction_date": first,
            "nav_change_since_first": dec(history[-1]["value"]) / dec(first_nav["value"]) - 1 if history and first_nav else None,
            "needs_reconciliation": True,
            "notice": "历史资料已纳入跟踪起点，份额与费用保留净值还原标记；后续申请、确认及到账按实际记录。" if tracking_started else ("历史交易保留为资料；当前持仓已按单独起点建账，起点前已实现收益待补。" if snapshot_started else "交易列表证据已保存；确认份额、确认日期、卖出费用及实际到账待核对，尚未计入正式持仓与现金。")})

    def t_analysis(self, c, fund_id, history=None, draft=None, strict=False):
        try:
            self.fund(c, fund_id)
            statement = Store.doc(c, "statement", fund_id)
            require(statement is not None, "STATEMENT_REQUIRED", "请先登记完整的交易资料")
            rule = self.rules(c, fund_id)
            saved = Store.doc(c, "t_pairing", fund_id)
            if saved and draft is None:
                require(saved["statement_version"] == statement["_version"] and saved["rule_version"] == rule["_version"],
                        "PAIRING_BASIS_CHANGED", "交易资料或规则已更新，请重新核对做T配对")
            source = Store.doc(c, "nav_sync", fund_id)
            require(not source or not source.get("corporate_actions"), "CORPORATE_ACTION_REVIEW", "来源含分红拆分，需先核对做T历史口径")
            require(not any(e["kind"] in {"DIVIDEND_DECLARED","DIVIDEND_REINVESTED","SPLIT"} and e["payload"].get("fund_id") == fund_id
                            for e in Store.events(c)), "CORPORATE_ACTION_REVIEW", "账本存在分红拆分，请先核对交易资料是否完整覆盖")
            ref = reconstruct(statement, history if history is not None else self.observations(c,fund_id,"OFFICIAL_NAV"),rule)
            config = draft if draft is not None else saved or {"method":"RECENT_BUY_FIRST"}
            method = config.get("method", "RECENT_BUY_FIRST")
            # Persisted auto suggestions are materialized into explicit pairs,
            # so a later quote cannot silently reassign the user's T trades.
            engine = "MANUAL" if saved and draft is None else method
            result = attribute(ref,engine,config.get("pairs",[]))
            extra = []
            if any(r.get('time_precision') == 'MINUTE' for r in statement['rows']):
                extra.append('截图仅到分钟；秒位00为存储兼容占位，不是实际成交秒数')
            if any(r.get('original_category') == 'ACTIVITY' for r in statement['rows']):
                extra.append('活动按用户要求计为买入与成本；参考净值日为单独留痕的还原假设，不是渠道确认')
            return dict(result,status="REFERENCE",method=method,fund_id=fund_id,
                statement_version=statement["_version"],rule_version=rule["_version"],pairing_version=saved["_version"] if saved else 0,
                pairing_saved=bool(saved) and draft is None,
                assumptions=["净值还原估算，尚未替代渠道确认和实际到账", "有效买入按当日净值成功确认，卖出金额按净到账额反推",
                    "持有期起点暂取申请次日，费用按FIFO批次和当前费率估算", "份额及金额按两位小数还原，金额反推份额可能存在同额解，暂用最小匹配份额",
                    "T配对用于分摊成本，配对前后总盈亏保持一致；赎回费不随T配对改变"] + extra,
                notice="最近加仓优先仅为配对假设，可改为卖后回补或自定义；没有后续回补的卖出保持未配对。")
        except Problem as exc:
            if strict:
                raise
            return {"status":"BLOCKED","code":exc.code,"notice":exc.message}

    def preview_t_analysis(self, fund_id, p):
        with self.store.connect() as c:
            return self.t_analysis(c,fund_id,draft=p,strict=True)

    def save_t_pairings(self, fund_id, p):
        with self.store.transaction() as c:
            require(fund_id not in self.state(c)['tracking'],'TRACKING_ALREADY_STARTED','历史已作为跟踪起点留存；请从审计更正起点，不重复修改旧配对')
            result = self.t_analysis(c,fund_id,draft=p,strict=True)
            require(p.get("statement_version") == result["statement_version"] and p.get("rule_version") == result["rule_version"],
                    "PAIRING_BASIS_CHANGED", "交易资料或费用版本已更新，请先重新预览")
            clean = {"fund_id":fund_id,"method":result["method"],"pairs":result["pairs"],
                     "statement_version":result["statement_version"],"rule_version":result["rule_version"]}
            old = Store.doc(c,"t_pairing",fund_id)
            if old and digest({k:v for k,v in old.items() if k not in {"_version","saved_at"}}) == digest(clean):
                return dict(old,duplicate=True)
            saved = Store.put(c,"t_pairing",fund_id,dict(clean,saved_at=self.now()),p.get("expected_version"))
            Store.put(c,"t_pairing_history",fund_id+":"+str(saved["_version"]),saved)
            return saved

    def bucket_analysis(self,c,fund_id,history=None,draft=None,strict=False):
        try:
            ref=self.t_analysis(c,fund_id,history,draft={'method':'NONE'},strict=True)
            saved=Store.doc(c,'bucket_classification',fund_id)
            if saved and draft is None:
                require(saved['statement_version']==ref['statement_version'] and saved['rule_version']==ref['rule_version'],
                        'BUCKET_BASIS_CHANGED','交易资料或费用规则已更新，请重新核对历史分仓')
            config=draft if draft is not None else saved
            if not config or config.get('method') in {'FROM_CURRENT_T','FROM_RECENT_T'}:
                prior=self.t_analysis(c,fund_id,history,
                    draft={'method':'RECENT_BUY_FIRST'} if config and config.get('method')=='FROM_RECENT_T' else None,strict=True)
                t_units={b['id']:ZERO for b in ref['buys']}
                for pair in prior['pairs']:t_units[pair['buy_id']]+=dec(pair['units'])
                assignments=[]
                for b in ref['buys']:
                    setting={'row_id':b['id'],'bucket':'TACTICAL' if t_units[b['id']] else 'BASE'}
                    if t_units[b['id']]:setting['t_units']=t_units[b['id']]
                    assignments.append(setting)
                paired={s['id'] for s in prior['sell_details'] if s['paired_units']}
                assignments += [{'row_id':s['id'],'bucket':'TACTICAL' if s['id'] in paired else 'BASE'} for s in ref['sells']]
                basis='沿用现有T复盘生成分仓；净值还原仍为估算'
            else:
                assignments=config.get('assignments')
                basis=str(config.get('source_basis','用户设置历史买卖的底仓/T归属'))[:300]
            result=reference_buckets(ref,assignments)
            return dict(result,status='REFERENCE',fund_id=fund_id,classification_version=saved['_version'] if saved else 0,
                        classification_saved=bool(saved) and draft is None,statement_version=ref['statement_version'],rule_version=ref['rule_version'],
                        source_basis=basis,assumptions=ref['assumptions'],
                        notice='做T卖出先扣T仓，不足补扣底仓；整笔净额减全部扣出成本，统一计入T盈亏。费用仍按原FIFO批次估算。')
        except Problem as exc:
            if strict:raise
            return {'status':'BLOCKED','code':exc.code,'notice':exc.message}

    def preview_bucket_analysis(self,fund_id,p):
        with self.store.connect() as c:return self.bucket_analysis(c,fund_id,draft=p,strict=True)

    def save_bucket_classification(self,fund_id,p):
        with self.store.transaction() as c:
            require(fund_id not in self.state(c)['tracking'],'TRACKING_ALREADY_STARTED','历史已作为跟踪起点留存；请从审计更正起点，不重复分类旧交易')
            result=self.bucket_analysis(c,fund_id,draft=p,strict=True)
            require(p.get('statement_version')==result['statement_version'] and p.get('rule_version')==result['rule_version'],
                    'BUCKET_BASIS_CHANGED','交易资料或费用规则已更新，请重新预览')
            clean={k:result[k] for k in ['fund_id','assignments','statement_version','rule_version','source_basis']}
            old=Store.doc(c,'bucket_classification',fund_id)
            if old and digest({k:v for k,v in old.items() if k not in {'_version','saved_at'}})==digest(clean):return dict(old,duplicate=True)
            saved=Store.put(c,'bucket_classification',fund_id,dict(clean,saved_at=self.now()),p.get('expected_version'))
            Store.put(c,'bucket_classification_history',fund_id+':'+str(saved['_version']),saved)
            return saved

    def refresh_public_nav(self, fund_id, *, fetcher=None, manual=False):
        require(self.mode == "real", "REAL_ONLY", "公开真实净值仅写入真实账户")
        with self.store.transaction() as c:
            fund = self.fund(c, fund_id)
            require(fund["fund_code"] in SUPPORTED and fund["share_class"] == "C", "SOURCE_NOT_SUPPORTED", "该基金及份额类别尚未核实公开净值来源")
            old = Store.doc(c, "nav_sync", fund_id)
            require_refresh_slot(old, self.now(), manual=manual)
            started = Store.put(c, "nav_sync", fund_id, dict(old or {}, attempted_at=self.now(), status="CHECKING",
                automatic_interval_seconds=AUTO_REFRESH_SECONDS,
                next_attempt_at=(stamp(self.now()) + timedelta(seconds=AUTO_REFRESH_SECONDS)).isoformat()), old["_version"] if old else None)
        try:
            data = (fetcher or fetch_public_nav)(fund["fund_code"], self.today())
            if data.get("raw_body"):
                cache = self.root / "data/cache/providers/daily_nav" / fund["fund_code"]
                cache.mkdir(parents=True, exist_ok=True)
                (cache / (data["raw_sha256"] + ".js")).write_bytes(data["raw_body"])
            with self.store.transaction() as c:
                inserted = 0
                for row in data["records"]:
                    result = self.observe({"fund_id":fund_id,"value_kind":"OFFICIAL_NAV", "effective_date":row["effective_date"],
                        "value": row["value"], "source":data["source"] + " · " + fund["fund_code"],
                        "quality":"DISTRIBUTOR_DISCLOSURE"}, c)
                    inserted += not result["duplicate"]
                current = Store.doc(c, "nav_sync", fund_id)
                actions = [r for r in data["records"] if r["corporate_action"]]
                complete = data.get("corporate_actions_complete", True)
                if not complete:
                    actions = actions + (current or {}).get("corporate_actions", [])
                saved = Store.put(c, "nav_sync", fund_id, {"status":"READY", "attempted_at":started["attempted_at"],
                    "automatic_interval_seconds":AUTO_REFRESH_SECONDS,"next_attempt_at":started["next_attempt_at"],
                    "last_success_at":self.now(),"source":data["source"],"source_url":data["source_url"],
                    "raw_sha256":data["raw_sha256"],"first_date":data["records"][0]["effective_date"],
                    "latest_date":data["records"][-1]["effective_date"],"count":len(data["records"]),
                    "provider_id":data.get("provider_id", "EASTMONEY_PUBLIC_NAV"), "fallback_errors":data.get("fallback_errors", []),
                    "freshness_source":data.get('freshness_source'), "history_source_url":data.get('history_source_url'),
                    "corporate_actions":actions, "corporate_actions_complete":complete}, current["_version"])
                self.auto_post_orders(c)
                self.capture_estimate_results(c,fund_id)
                return dict(saved, inserted=inserted)
        except Exception as exc:
            with self.store.transaction() as c:
                current = Store.doc(c, "nav_sync", fund_id)
                Store.put(c, "nav_sync", fund_id, dict(current, status="FAILED", error_code=getattr(exc,"code","SOURCE_UNAVAILABLE")), current["_version"])
            raise
