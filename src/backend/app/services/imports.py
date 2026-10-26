from __future__ import annotations

import csv
import io
import json

from app.domain.finance import Problem, require, stamp, day
from app.persistence.store import Store, pack, digest


class ImportMixin:
    def _import_row(self, c, kind, row, file_hash, index):
        def fund_id():
            if row.get("fund_id"):
                self.fund(c, row["fund_id"])
                return row["fund_id"]
            matches = [f for f in Store.docs(c, "fund") if f.get("fund_code") == row.get("fund_code") and
                       (not row.get("share_class") or f["share_class"] == row["share_class"])]
            require(len(matches) == 1, "FUND_MAPPING_REQUIRED", "代码未登记或对应多个份额/渠道，请先登记基金并使用 fund_id 明确映射")
            return matches[0]["id"]

        key = row.get("idempotency_key") or row.get("external_event_id") or f"import:{file_hash}:{index}"
        if row.get("account_id"):
            require(row["account_id"] in {"local", "default", self.mode}, "ACCOUNT_MAPPING_REQUIRED", "首版每个模式跟踪一个 local 账户；请先明确账户范围再映射 account_id")
        at = row.get("effective_at") or row.get("submitted_at") or row.get("occurred_at")
        if kind == "funds":
            code = row["fund_code"]
            require(len(code) == 6 and code.isdigit(), "INVALID_FUND_CODE", "基金代码须为六位字符串")
            require(bool(row.get("name")), "INVALID_NAME", "基金名称不能为空")
            require((row.get("quantity_unit") or "FUND_UNIT") == "FUND_UNIT" and (row.get("currency") or "CNY") == "CNY",
                    "UNIT_NOT_SUPPORTED", "首版持仓核算仅支持人民币基金份额，不能将克数或外币静默转换")
            id = "fund_" + digest([code, row.get("share_class") or "A", row.get("channel") or "手工记录"])[:16]
            old = Store.doc(c, "fund", id)
            if not old:
                Store.put(c, "fund", id, {"id": id, "fund_code": code, "name": row["name"], "share_class": row.get("share_class") or "A",
                     "channel": row.get("channel") or "手工记录", "instrument_type": row.get("instrument_type") or "OPEN_END_FUND",
                     "quantity_unit": "FUND_UNIT", "currency": "CNY", "watching": True,
                     "rule_source_url": row.get("rule_source_url") or None, "rule_verified_at": row.get("rule_verified_at") or None})
            return {"fund_id": id}
        if kind == "observations":
            return self.observe(dict(row, fund_id=fund_id(), value_kind=row.get("value_kind") or "OFFICIAL_NAV"), c)
        if kind == "lots":
            id = fund_id()
            require(bool(row.get("lot_external_id")), "LOT_ID_REQUIRED", "请填写唯一的期初批次编号")
            snapshot = row.get("snapshot_date") or self.today()
            day(snapshot)
            require(not any(o["fund_id"] == id and o["side"] == "BUY" and o["confirmed_units"]
                            for o in self.state(c)["orders"].values()), "HISTORY_OVERLAP", "已有确认申购，不能再叠加期初持仓；请先明确重建范围")
            return self.event("OPENING_LOT", {"fund_id": id, "lot_id": row["lot_external_id"], "units": row["confirmed_units"],
                "cost": row.get("remaining_cost_cny") or None, "acquired": row.get("purchase_confirm_date") or None,
                "available_from": row.get("available_from") or None, "snapshot_date": snapshot, "time_precision": "DAY", "completeness": row.get("completeness") or None,
                "bucket":row.get('bucket') or 'BASE'},
                "opening:" + id + ":" + row["lot_external_id"], snapshot + "T00:00:00+08:00", c=c)
        if kind == "cash_events":
            event_type = {"DEPOSIT": "CASH_DEPOSIT", "WITHDRAWAL": "CASH_WITHDRAWAL", "SETTLED": "ORDER_SETTLED",
                          "PAID": "ORDER_PAID", "REFUNDED": "ORDER_REFUNDED"}.get(row["event_type"], row["event_type"])
            require(event_type in {"CASH_DEPOSIT", "CASH_WITHDRAWAL", "ORDER_SETTLED", "ORDER_PAID", "ORDER_REFUNDED"}, "INVALID_EVENT", "不支持的现金事件类型")
            payload = {"amount": row["amount_cny"], "note": row.get("notes", "")}
            if row.get("related_order_id"):
                payload["order_id"] = self._resolve_order(c, row["related_order_id"])
            require(at is not None, "INVALID_TIME", "现金事件需要含时区的 effective_at")
            return self.event(event_type, payload, "cash:" + key, at, c=c)
        if kind == "orders":
            require(bool(row.get("external_order_id")), "ORDER_ID_REQUIRED", "导入申请需有唯一 external_order_id")
            require(at is not None, "INVALID_TIME", "导入申请须填写含时区的 submitted_at")
            require(row.get("submission_status", "") in {"", "SUBMITTED", "ACCEPTED"}, "IMPORT_STATE_UNSUPPORTED", "申请导入支持已提交或已受理记录；请将确认/拒绝/撤销结果逐事件导入")
            require(row.get("payment_state", "") in {"", "UNPAID", "PAID"}, "IMPORT_STATE_UNSUPPORTED", "扣款状态须为 UNPAID 或 PAID，部分扣款请另导入现金事件")
            external = row["external_order_id"]
            prior = [o for o in self.state(c)["orders"].values() if o.get("external_order_id") == external]
            if prior:
                require(prior[0]["fund_id"] == fund_id() and prior[0]["side"] == row["side"], "DUPLICATE_EXTERNAL_ORDER", "已有渠道订单号与本行内容冲突")
                require(prior[0].get('bucket','BASE')==(row.get('bucket') or 'BASE'),'DUPLICATE_EXTERNAL_ORDER','同一渠道订单号的底仓/T归属不一致')
                if row.get('cash_source'):
                    require(prior[0].get('cash_source','TRACKED')==row['cash_source'],'DUPLICATE_EXTERNAL_ORDER','同一渠道订单号的资金范围不一致')
                from app.domain.finance import dec
                require(prior[0]["requested"] == dec(row["requested_amount_cny"] if row["side"] == "BUY" else row["requested_units"]),
                        "DUPLICATE_EXTERNAL_ORDER", "相同渠道订单号的申请数量不一致，不能按重复行忽略")
                return {"order_id": prior[0]["id"], "duplicate": True}
            return self.order({"fund_id": fund_id(), "side": row["side"], "requested": row["requested_amount_cny"] if row["side"] == "BUY" else row["requested_units"],
                "external_order_id": external, "key": "order:" + external, "occurred_at": at,
                "submission_status": row.get("submission_status") or "SUBMITTED", "paid_immediately": row.get("payment_state") == "PAID",
                "bucket":row.get('bucket') or 'BASE',
                **({'cash_source':row['cash_source']} if row.get('cash_source') else {})}, c)
        if kind == "confirmations":
            oid = self._resolve_order(c, row["external_order_id"])
            event_type = row.get("event_type") or "ORDER_CONFIRMED"
            event_type = {"CONFIRMED": "ORDER_CONFIRMED", "SETTLED": "ORDER_SETTLED", "PAID": "ORDER_PAID",
                          "CANCELED": "ORDER_CANCELED", "REJECTED": "ORDER_REJECTED", "REFUNDED": "ORDER_REFUNDED"}.get(event_type, event_type)
            payload = {"order_id": oid}
            if not at:
                require(bool(row.get("confirm_date")), "INVALID_DATE", "请填写确认日期或明确的业务时间")
                at = row["confirm_date"] + "T00:00:00+08:00"
                payload["time_precision"] = "DAY"
            if event_type == "ORDER_CONFIRMED":
                ext = row.get("confirmation_external_id") or row.get("confirmation_sequence")
                require(bool(ext) or row.get("idempotency_key"), "CONFIRMATION_ID_REQUIRED", "逐条确认须有唯一编号、序号或幂等键")
                payload.update(units=row["units_delta"], nav=row["confirmed_nav"], gross=row["gross_amount_delta_cny"],
                    fee=row["fee_delta_cny"], net=row["net_amount_delta_cny"], nav_date=row["nav_date"],
                    confirm_date=row["confirm_date"], available_from=row.get("available_from") or None,
                    is_final=row.get("is_final_for_order", "true").lower() in {"true", "1", "yes"},
                    confirmation_external_id=ext, source_record_ref=row.get("source_record_ref") or None, notes=row.get("notes") or None)
                key = "confirmation:" + oid + ":" + (ext or key)
            else:
                payload["amount"] = row.get("net_amount_delta_cny") or row.get("gross_amount_delta_cny")
            return self.event(event_type, payload, key, at, c=c)
        raise Problem("IMPORT_TYPE_UNSUPPORTED", "不支持此导入类型")

    def _resolve_order(self, c, external):
        orders = self.state(c)["orders"]
        if external in orders:
            return external
        matches = [o for o in orders.values() if o.get("external_order_id") == external]
        require(len(matches) == 1, "ORDER_NOT_FOUND", "关联渠道订单不存在，请先导入申请")
        return matches[0]["id"]

    def import_preview(self, kind, content):
        require(kind in {"funds", "observations", "lots", "cash_events", "orders", "confirmations"}, "IMPORT_TYPE_UNSUPPORTED", "未知导入类型")
        require(isinstance(content, str) and len(content.encode()) <= 2_000_000, "IMPORT_TOO_LARGE", "文件大小应不超过 2 MB")
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)), "CSV_HEADERS", "CSV 表头为空或重复")
        rows = list(reader)
        require(0 < len(rows) <= 1000, "IMPORT_ROW_LIMIT", "每次导入 1–1000 行")
        rows = [{str(k).strip(): (v or "").strip() for k, v in row.items() if k is not None} for row in rows]
        file_hash, errors = digest(content), []
        with self.store.transaction() as c:
            revision = self.state(c)["revision"]
            doc_hash = digest([Store.docs(c, "fund"), Store.docs(c, "rule")])
            c.execute("SAVEPOINT preview")
            for i, row in enumerate(rows, 2):
                c.execute("SAVEPOINT row_preview")
                try:
                    self._import_row(c, kind, row, file_hash, i)
                except (Problem, KeyError, TypeError, ValueError) as exc:
                    c.execute("ROLLBACK TO row_preview")
                    errors.append({"line": i, "code": exc.code if isinstance(exc, Problem) else "CSV_FIELD_MISSING",
                                   "message": exc.message if isinstance(exc, Problem) else "字段缺失或格式错误：" + str(exc)})
                c.execute("RELEASE row_preview")
            c.execute("ROLLBACK TO preview")
            c.execute("RELEASE preview")
            id = "imp_" + digest([kind, file_hash, revision, doc_hash, self.mode])[:24]
            payload = {"id": id, "kind": kind, "mode": self.mode, "source_hash": file_hash, "revision": revision,
                       "document_hash": doc_hash, "rows": rows, "errors": errors, "count": len(rows), "created_at": self.now()}
            c.execute("INSERT OR IGNORE INTO imports VALUES(?,?,?)", (id, pack(payload), self.now()))
            return payload

    def import_commit(self, id, source_hash):
        with self.store.transaction() as c:
            row = c.execute("SELECT payload FROM imports WHERE id=?", (id,)).fetchone()
            require(row is not None, "IMPORT_NOT_FOUND", "导入预览不存在")
            preview = json.loads(row[0])
            require(preview["mode"] == self.mode and source_hash == preview["source_hash"], "IMPORT_HASH_MISMATCH", "导入文件或模式与预览不一致")
            require(not preview["errors"], "IMPORT_HAS_ERRORS", "请先修正预览中的全部错误再导入")
            prior = Store.doc(c, "import_commit", id)
            if prior:
                return dict(prior, duplicate=True)
            require(preview["revision"] == self.state(c)["revision"] and preview["document_hash"] == digest([Store.docs(c, "fund"), Store.docs(c, "rule")]),
                    "VERSION_CONFLICT", "预览后账本或基金规则已变化，请重新预览")
            for index, row in enumerate(preview["rows"], 2):
                self._import_row(c, preview["kind"], row, preview["source_hash"], index)
            result = {"id": id, "count": preview["count"], "committed_at": self.now(), "revision": self.state(c)["revision"]}
            Store.put(c, "import_commit", id, result)
            return result
