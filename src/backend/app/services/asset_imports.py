"""Read normalized single-product packages. Evidence is never a fill."""
from datetime import timedelta
from decimal import Decimal
import json
import re

from app.domain.finance import ZERO, Problem, amount, dec, day, money, require, stamp, SHANGHAI
from app.persistence.store import Store, digest, pack
from app.schemas.asset_import import AssetDataBatch, TRANSACTION_METADATA, transaction_metadata

BATCH_KIND = 'NORMALIZED_SINGLE_ASSET_V1'


def catalog():
    return {
        'schema_version': 1, 'one_asset_per_batch': True,
        'asset_kinds': ['FUND', 'GOLD_FUND', 'GOLD_GRAM'],
        'input_format': 'CODEX_NORMALIZED_JSON',
        'source_handling': 'metadata only; original files remain outside the app import pipeline',
        'transaction_limit': 1000,
        'fund_position': 'EXPLICIT_CURRENT_SNAPSHOT_ONLY',
        'gold_gram': 'SEPARATE_REPORTED_HOLDINGS_AND_EVIDENCE; NOT_IN_FUND_LEDGER_OR_PORTFOLIO',
        'money_encoding': 'decimal strings; missing = null; never infer shares from currency',
        'closed_transactions': 'Codex removes invalid/closed rows before submission',
        'duplicate_rows': 'normalized rows must be unique; the app does not clean them',
        'preview_schema': AssetDataBatch.model_json_schema(),
    }


def merge_statement(old, new):
    if not old:
        return new
    sources = {s['sha256']: s for s in old['sources']}
    sources.update({s['sha256']: s for s in new['sources']})
    rows = old['rows'] + new['rows']
    keys = [digest([r['displayed_at'],r['side'],r['unit'],format(dec(r['value']).normalize(),'f')]) for r in rows]
    require(len(set(keys)) == len(keys), 'NORMALIZED_ROW_DUPLICATE', '标准数据包含已有记录，请由 Codex 整理为新增记录后提交')
    return dict(new, sources=list(sources.values()), rows=rows, expected_version=old['_version'])



class AssetImportMixin:
    def _asset_id(self, c, asset):
        if asset['kind'] == 'GOLD_GRAM':
            return 'gold_' + digest([asset['channel'], asset['product_code']])[:20]
        matches = [f for f in Store.docs(c,'fund') if (f.get('fund_code'),f.get('share_class'),f.get('channel')) ==
                   (asset['fund_code'],asset['share_class'],asset['channel'])]
        require(len(matches) <= 1, 'ASSET_AMBIGUOUS', '产品身份对应多条档案，需先核对重复档案')
        if asset.get('fund_id'):
            require(len(matches) == 1 and matches[0]['id'] == asset['fund_id'], 'ASSET_MISMATCH', '基金 ID 与代码、类别或渠道不一致')
        return matches[0]['id'] if matches else 'fund_' + digest([asset['fund_code'],asset['share_class'],asset['channel']])[:16]

    def _basis(self, c, aid):
        return digest({'revision':self.state(c)['revision'], 'funds':Store.docs(c,'fund'),
            'asset':Store.doc(c,'gold_product',aid), 'statement':Store.doc(c,'statement',aid),
            'gold_statement':Store.doc(c,'gold_statement',aid), 'holding':Store.doc(c,'reported_holding',aid),
            'opening':Store.doc(c,'migration_opening',aid),
            'navs':[(r['id'],r['raw_hash']) for r in c.execute("SELECT id,raw_hash FROM observations WHERE fund_id=? AND value_kind='OFFICIAL_NAV' ORDER BY id",(aid,))]})

    def _refs(self, refs, source_map):
        result = []
        for ref in refs:
            require(ref['sha256'] in source_map, 'SOURCE_REFERENCE', '记录引用了本批次之外的截图')
            result.append({'sha256':ref['sha256'],'row':ref['row']})
        return sorted(result,key=lambda r:(r['sha256'],r['row']))

    def _rows(self, manifest, aid, source_map):
        rows, errors = [], []
        gram = manifest['asset']['kind'] == 'GOLD_GRAM'
        for i, row in enumerate(manifest['transactions']):
            try:
                require(row['unit'] in ({'CNY','GRAM'} if gram else {'CNY','FUND_UNIT'}), 'UNIT_MISMATCH', '交易单位与产品不一致')
                require(gram or row['side'] != 'BUY' or row['unit'] == 'CNY', 'UNIT_MISMATCH', '基金列表的买入按人民币金额登记')
                when = stamp(row['displayed_at'].replace(' ','T')+'+08:00')
                require(when <= stamp(self.now()), 'FUTURE_EVENT', '交易时间不能晚于当前时间')
                require(row['status'] != 'PENDING_SETTLEMENT' or row['side'] == 'SELL', 'STATEMENT_ROW_INVALID', '预计到账仅适用于卖出')
                if row.get('expected_settlement_date'):
                    require(day(row['expected_settlement_date']) >= when.astimezone(SHANGHAI).date(), 'INVALID_DATE', '预计到账早于交易日期')
                value = amount(row['value'],positive=True) if row['unit'] == 'CNY' else dec(row['value'],positive=True)
                metadata = transaction_metadata(row)
                require(not gram or not metadata.get('reference_nav_date'), 'UNIT_MISMATCH', '按克黄金不能指定基金参考净值日')
                refs = self._refs(row['source_refs'],source_map)
                rows.append({'id':'statement_row_'+digest([aid,row['displayed_at'],row['side'],row['unit'],format(value.normalize(),'f')])[:20],
                    'displayed_at':row['displayed_at'],'side':row['side'],'unit':row['unit'],'value':value,
                    'status':row['status'],'source_refs':refs,'expected_settlement_date':row.get('expected_settlement_date'),'status_text':'', **metadata})
            except Problem as exc:
                errors.append({'path':f'transactions.{i}', 'code':exc.code, 'message':exc.message})
        return rows, errors

    def _apply_batch(self, c, manifest, aid, source_map, initialize=False):
        asset = manifest['asset']; gram = asset['kind'] == 'GOLD_GRAM'
        if gram:
            old = Store.doc(c,'gold_product',aid)
            if not old:
                Store.put(c,'gold_product',aid,dict(asset,id=aid,quantity_unit='GRAM',currency='CNY'))
        else:
            old = Store.doc(c,'fund',aid)
            if not old:
                self.save_fund({'fund_code':asset['fund_code'],'share_class':asset['share_class'], 'name':asset['name'],
                    'channel':asset['channel'],'instrument_type':'GOLD_LINKED' if asset['kind']=='GOLD_FUND' else 'OPEN_END_FUND'},c)
        rows, errors = self._rows(manifest,aid,source_map)
        if errors: return {'errors':errors}
        require(rows or manifest.get('position') or manifest['official_navs'],'IMPORT_EMPTY','没有有效交易、持仓或净值数据')
        if rows:
            keys = [(r['displayed_at'],r['side'],r['unit'],format(dec(r['value']).normalize(),'f')) for r in rows]
            require(len(keys)==len(set(keys)),'NORMALIZED_ROW_DUPLICATE','标准数据中有重复交易，请先整理为唯一记录')
            kind = 'gold_statement' if gram else 'statement'
            previous = Store.doc(c,kind,aid)
            merged = merge_statement(previous, {'fund_id':aid, 'channel':asset['channel'],
                'sources':[{'sha256':s['sha256'],'filename':s['filename']} for s in source_map.values()],
                'rows':rows,'history_complete':manifest['history_complete'],
                'history_completeness_basis':manifest['history_completeness_basis']})
            if gram:
                saved = dict(merged, quantity_unit='GRAM', timezone='Asia/Shanghai', ledger_posted=False)
                saved.pop('expected_version',None)
                # Repeated identical receipts do not add statement versions.
                if not previous or digest({k:v for k,v in previous.items() if k!='_version'}) != digest(saved):
                    Store.put(c,kind,aid,saved,previous['_version'] if previous else None)
            else:
                self.save_statement(merged,c)
        for i, nav in enumerate(manifest['official_navs']):
            require(not gram, 'UNIT_MISMATCH', '按克黄金不能导入基金净值')
            self._refs(nav['source_refs'],source_map)
            existing = self.observations(c,aid,'OFFICIAL_NAV')
            same = next((r for r in existing if r['effective_date']==nav['effective_date']),None)
            require(not same or dec(same['value']) == dec(nav['value']), 'NAV_CONFLICT', f'official_navs.{i} 与已存正式净值冲突，请单独核对')
            if not same:
                self.observe({'fund_id':aid, 'value_kind':'OFFICIAL_NAV','effective_date':nav['effective_date'],
                    'value':nav['value'],'source':'支付宝截图人工核对','quality':'MANUAL'},c)
        position = manifest.get('position')
        if position:
            expected_unit = 'GRAM' if gram else 'FUND_UNIT'
            require(not gram or position.get('base_units') is None,'GOLD_LEDGER_SEPARATE','按克黄金暂保存独立持仓，不套用基金分仓字段')
            require(position['unit']==expected_unit, 'UNIT_MISMATCH', '持仓单位与产品不一致')
            require(position['snapshot_date'] <= self.today(), 'FUTURE_EVENT', '持仓日期不能晚于当前日期')
            self._refs(position['source_refs'],source_map)
            for key in ['remaining_cost_cny','reported_value_cny']:
                if position.get(key) is not None: amount(position[key])
            if position.get('acquired_date'):
                require(position['acquired_date'] <= position['snapshot_date'], 'INVALID_DATE', '购入日期晚于持仓日期')
            require(not position.get('acquired_date') or not position.get('available_from') or position['available_from'] >= position['acquired_date'], 'INVALID_DATE', '可卖日期早于购入日期')
            old_holding = Store.doc(c,'reported_holding',aid)
            clean = dict(position,asset_id=aid,asset_kind=asset['kind'],sources=manifest['sources'],basis='SCREENSHOT_REPORTED',ledger_posted=False)
            if not old_holding or digest({k:v for k,v in old_holding.items() if k!='_version'}) != digest(clean):
                Store.put(c,'reported_holding',aid,clean,old_holding['_version'] if old_holding else None)
            if initialize:
                require(not gram, 'GOLD_LEDGER_SEPARATE', '按克黄金已独立保存持仓资料，不写基金份额账本')
                self._open_imported_position(c,aid,position,manifest)
        else:
            require(not initialize,'POSITION_REQUIRED','本批次没有当前持仓截图数据')
        statement = Store.doc(c,'gold_statement' if gram else 'statement',aid)
        return {'asset_id':aid, 'asset_kind':asset['kind'], 'new_asset':not bool(old),
            'transaction_count':len(rows), 'merged_transaction_count':len(statement['rows']) if statement else 0,
            'position_recorded':bool(position), 'position_initialized':initialize, 'errors':[]}

    def _open_imported_position(self,c,aid,p,manifest):
        state = self.state(c)
        require(not any(l['fund_id']==aid for l in state['lots'].values()) and
                not any(o['fund_id']==aid for o in state['orders'].values()) and aid not in state['tracking'] and
                not Store.doc(c,'migration_opening',aid), 'HISTORY_OVERLAP','此产品已建账，禁止叠加另一份当前持仓')
        units = dec(p['units'])
        require(p.get('base_units') is not None,'BUCKET_REQUIRED','请明确底仓份额，剩余份额归做 T，不自动替你分仓')
        base = dec(p['base_units'])
        require(base <= units, 'BASE_UNITS_EXCESS','底仓份额超过总份额')
        navs = [r for r in self.observations(c,aid,'OFFICIAL_NAV') if r['effective_date'] <= p['snapshot_date']]
        if navs and p.get('reported_value_cny') is not None:
            require(abs(money(units*dec(navs[-1]['value']))-amount(p['reported_value_cny'])) <= Decimal('0.02'),
                    'POSITION_VALUE_MISMATCH','份额乘参考净值与截图金额不一致，请核对日期和在途记录')
        cost = amount(p['remaining_cost_cny']) if p.get('remaining_cost_cny') is not None else None
        require(units > 0 or cost in {None,ZERO},'POSITION_COST','零份额不能有剩余成本')
        base_cost = (cost if base==units else money(cost*base/units)) if cost is not None and units else cost
        for bucket, q, value in [('BASE',base,base_cost),('TACTICAL',units-base,None if cost is None else cost-(base_cost or ZERO))]:
            if q:
                lot_id = 'migration-'+digest([aid,manifest['client_reference'],bucket])[:20]
                self.event('OPENING_LOT',{'fund_id':aid,'lot_id':lot_id,'units':q,'cost':value,
                    'acquired':p.get('acquired_date'),'available_from':p.get('available_from'),
                    'snapshot_date':p['snapshot_date'],'time_precision':'DAY','bucket':bucket,
                    'source_batch':manifest['client_reference']},'opening:'+aid+':'+lot_id,
                    p['snapshot_date']+'T00:00:00+08:00',c=c)
        Store.put(c,'migration_opening',aid,{'asset_id':aid,'snapshot_date':p['snapshot_date'],
            'client_reference':manifest['client_reference'],'basis':'USER_REVIEWED_CURRENT_SNAPSHOT',
            'pnl_scope':'FROM_IMPORTED_COST; PRIOR_REALIZED_PROFIT_NOT_RECONSTRUCTED', 'sources':manifest['sources']})

    def preview_asset_batch(self, manifest):
        # Validate outside HTTP too (CLI and tests use the same service contract).
        p = AssetDataBatch.model_validate(manifest).model_dump(mode='json')
        # Keep v1 committed batch hashes stable when optional annotations are
        # absent. Do not strip pre-existing defaults from the canonical form.
        for row in p['transactions']:
            for key in TRANSACTION_METADATA:
                if row.get(key) is None:
                    row.pop(key, None)
        sid = 'batch_'+digest([self.mode,p['client_reference']])[:24]
        source_hash = digest(p)
        with self.store.transaction() as c:
            prior = c.execute('SELECT payload FROM imports WHERE id=?',(sid,)).fetchone()
            old = json.loads(prior['payload']) if prior else None
            if old and old['status']=='COMMITTED':
                require(old['source_hash']==source_hash,'IDEMPOTENCY_CONFLICT','已提交的批次编号不能用于不同内容')
                return dict(old,duplicate=True)
            sources = {source['sha256']:source for source in p['sources']}
            require(len(sources)==len(p['sources']),'SOURCE_DUPLICATE','资料来源清单重复，请提供已整理的标准数据')
            aid = self._asset_id(c,p['asset'])
            basis = self._basis(c,aid)
            c.execute('SAVEPOINT asset_preview')
            errors, opening_errors, summary = [], [], {}
            try:
                summary = self._apply_batch(c,p,aid,sources)
                errors = summary.pop('errors')
                if not errors and p.get('position') and p['asset']['kind']!='GOLD_GRAM':
                    try: self._open_imported_position(c,aid,p['position'],p)
                    except Problem as exc: opening_errors.append({'path':'position','code':exc.code,'message':exc.message})
            except (Problem,KeyError,TypeError,ValueError) as exc:
                errors.append({'path':'asset_data','code':exc.code if isinstance(exc,Problem) else 'INVALID_INPUT',
                               'message':exc.message if isinstance(exc,Problem) else '字段缺失或格式错误'})
            finally:
                c.execute('ROLLBACK TO asset_preview'); c.execute('RELEASE asset_preview')
            warnings = []
            if p['asset']['kind']=='GOLD_GRAM': warnings.append('按克黄金独立保存截图与持仓，不计入基金组合、不生成基金交易或做 T 金额')
            if not p['history_complete']: warnings.append('历史完整性未确认，不能据此计算完整历史累计收益')
            if p.get('position'): warnings.append('当前持仓需显式选择建账；历史交易仅为资料，不重复回放。成本或购入日期未知时保持未知')
            payload = {'id':sid,'kind':BATCH_KIND,'mode':self.mode,'status':'PREVIEWED', 'source_hash':source_hash,
                'preview_token':digest([source_hash,basis]),'basis_hash':basis,'asset_id':aid,'manifest':p,
                'summary':summary,'errors':errors,'position_errors':opening_errors,
                'can_commit_evidence':not bool(errors),'can_initialize_position':bool(p.get('position')) and p['asset']['kind']!='GOLD_GRAM' and not errors and not opening_errors,
                'warnings':warnings,'created_at':old['created_at'] if old else self.now(),
                'previewed_at':self.now(),'expires_at':(stamp(self.now())+timedelta(hours=24)).isoformat()}
            c.execute('INSERT INTO imports VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (sid,pack(payload),payload['created_at']))
            return payload

    def asset_batch(self,sid):
        require(bool(re.fullmatch(r'batch_[0-9a-f]{24}',sid)),'IMPORT_NOT_FOUND','批次编号无效')
        with self.store.connect() as c:
            row = c.execute('SELECT payload FROM imports WHERE id=?',(sid,)).fetchone()
            require(row is not None,'IMPORT_NOT_FOUND','当前账户没有此批次')
            data = json.loads(row['payload'])
            require(data['kind']==BATCH_KIND,'IMPORT_TYPE_UNSUPPORTED','此编号不是截图导入批次')
            return data

    def asset_batches(self,limit=20,offset=0):
        require(type(limit) is int and 1 <= limit <= 100 and type(offset) is int and offset >= 0,'PAGE_LIMIT','分页范围无效')
        with self.store.connect() as c:
            total = c.execute("SELECT COUNT(*) FROM imports WHERE json_extract(payload,'$.kind')=?",(BATCH_KIND,)).fetchone()[0]
            rows = c.execute("SELECT payload FROM imports WHERE json_extract(payload,'$.kind')=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",(BATCH_KIND,limit,offset)).fetchall()
        return {'total':total,'limit':limit,'offset':offset,'items':[{k:v for k,v in json.loads(r['payload']).items() if k not in {'manifest','basis_hash'}} for r in rows]}

    def commit_asset_batch(self,sid,p):
        require(p.get('confirm') is True,'IMPORT_CONFIRMATION','请先核对预览，再明确提交')
        preview = self.asset_batch(sid)
        require(preview['source_hash']==p['source_hash'] and preview['preview_token']==p['preview_token'],'IMPORT_HASH_MISMATCH','内容或预览版本不一致')
        if preview['status']=='COMMITTED':
            require(preview['result']['position_initialized']==p.get('initialize_position',False),'IDEMPOTENCY_CONFLICT','重复提交不能改变是否建账')
            return dict(preview['result'],duplicate=True)
        require(not preview['errors'],'IMPORT_HAS_ERRORS','请先修正全部预览错误')
        require(not p.get('initialize_position') or preview['can_initialize_position'],'POSITION_NOT_READY','当前持仓不能建账，请核对 position_errors 或产品单位')
        require(stamp(self.now()) < stamp(preview['expires_at']),'IMPORT_EXPIRED','预览超过 24 小时，请重新预览')
        # A consistent safety snapshot precedes the atomic commit. A rare race
        # can leave an unused backup, but can never partly import a batch.
        with self.store.connect() as c:
            require(self._basis(c,preview['asset_id'])==preview['basis_hash'],'VERSION_CONFLICT','预览后账本或产品资料变化，请重新预览')
        backup = self.create_backup()
        with self.store.transaction() as c:
            current = json.loads(c.execute('SELECT payload FROM imports WHERE id=?',(sid,)).fetchone()['payload'])
            require(current['preview_token']==p['preview_token'],'VERSION_CONFLICT','批次预览已变化，请重新读取')
            if current['status']=='COMMITTED':
                require(current['result']['position_initialized']==p.get('initialize_position',False),'IDEMPOTENCY_CONFLICT','重复提交不能改变是否建账')
                return dict(current['result'],duplicate=True)
            require(stamp(self.now()) < stamp(current['expires_at']),'IMPORT_EXPIRED','预览已过期，请重新预览')
            require(self._basis(c,preview['asset_id'])==preview['basis_hash'],'VERSION_CONFLICT','预览后账本或资料变化，请重新预览')
            sources = {source['sha256']:source for source in preview['manifest']['sources']}
            result = self._apply_batch(c,preview['manifest'],preview['asset_id'],sources,p.get('initialize_position',False))
            require(not result['errors'],'IMPORT_HAS_ERRORS','提交校验未通过，已回滚')
            result.update(id=sid,mode=self.mode,revision=self.state(c)['revision'],backup_id=backup['id'],committed_at=self.now())
            current.update(status='COMMITTED',result=result)
            c.execute('UPDATE imports SET payload=? WHERE id=?',(pack(current),sid))
            return result

    def imported_asset(self,aid):
        with self.store.connect() as c:
            asset = Store.doc(c,'fund',aid) or Store.doc(c,'gold_product',aid)
            require(asset is not None,'ASSET_NOT_FOUND','当前账户没有此产品')
            gram = asset.get('quantity_unit')=='GRAM'
            return {'asset':asset,'holding':Store.doc(c,'reported_holding',aid),
                'transactions':Store.doc(c,'gold_statement' if gram else 'statement',aid),
                'opening':Store.doc(c,'migration_opening',aid),'included_in_fund_portfolio':not gram}

    def gold_products(self):
        with self.store.connect() as c:
            items = []
            for product in Store.docs(c,'gold_product'):
                holding = Store.doc(c,'reported_holding',product['id'])
                statement = Store.doc(c,'gold_statement',product['id'])
                pnl = None
                if holding and holding.get('remaining_cost_cny') is not None and holding.get('reported_value_cny') is not None:
                    pnl = amount(holding['reported_value_cny']) - amount(holding['remaining_cost_cny'])
                items.append(dict(product,holding=holding,statement=statement,reported_holding_pnl_cny=pnl,
                    scope='REPORTED_SNAPSHOT_ONLY',included_in_fund_portfolio=False))
            return items
