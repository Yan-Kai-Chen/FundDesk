"""Read-only numeric receipts and bounded close-estimate accuracy evidence."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import json

from app.domain.finance import ZERO, dec, require, stamp
from app.domain.quote_phase import closing_reference
from app.domain.valuation_explanation import explain_fund, explain_portfolio
from app.persistence.store import Store, digest
from app.services.forecasting import FORMAL_QUALITIES

DEVIATION_KIND='close_estimate_result'
DEVIATION_LIMIT=30


def compact_quote(row):
    p=row.get('provenance') or {}
    return {k:row.get(k) for k in ('id','value','value_kind','effective_date','source','as_of','observed_at','published_at','quality','source_receipt_hash')} | {
        'provider_id':p.get('provider_id'),'reference_date':p.get('reference_date'),
        'reference_nav':p.get('reference_nav'),'source_url':p.get('source_url')}


def comparison(estimate, formal, basis_verified=True):
    value,actual=dec(estimate['value'],positive=True),dec(formal['value'],positive=True)
    ref=estimate.get('reference_nav')
    basis=dec(ref,positive=True) if ref is not None and basis_verified else None
    ratio=lambda x:x.quantize(Decimal('.000000000001'),rounding=ROUND_HALF_UP)
    return {'nav_difference':value-actual,'relative_error':ratio(value/actual-1),'basis_verified':basis is not None,
        'estimated_change':ratio(value/basis-1) if basis else None,
        'formal_change':ratio(actual/basis-1) if basis else None,
        'change_error_pp':ratio((value-actual)/basis*100) if basis else None}


class ValuationExplanationMixin:
    def capture_estimate_results(self,c=None,fund_id=None):
        """Called on collection/maintenance only. Never invoked from a GET.

        A pre-first-formal-receipt close is frozen at first resolution.
        Later NAV revisions update a separate comparison, preserving the first.
        Each fund keeps at most30 compact receipts, independent of minute cache.
        """
        if c is None:
            with self.store.transaction() as connection:
                return self.capture_estimate_results(connection,fund_id)
        ids=[fund_id] if fund_id else [f['id'] for f in Store.docs(c,'fund')]
        cutoff=(stamp(self.now())-timedelta(days=45)).date().isoformat()
        changed=0
        for fid in ids:
            closes={};formals={}
            rows=c.execute('SELECT id,payload FROM observations WHERE fund_id=? AND effective_date>=? ORDER BY observed_at,rowid',(fid,cutoff))
            for row in rows:
                item=dict(json.loads(row['payload']),id=row['id']);date=item['effective_date']
                if item['value_kind']=='OFFICIAL_NAV' and item.get('quality') in FORMAL_QUALITIES:
                    formals.setdefault(date,[]).append(item)
                elif closing_reference(item,date):
                    closes.setdefault(date,[]).append(item)
            existing={r['date']:r for r in Store.docs(c,DEVIATION_KIND) if r['fund_id']==fid}
            for date in sorted(set(closes)|set(existing)):
                old=existing.get(date)
                if old and old.get('formal_first'):
                    if not formals.get(date):continue
                    latest=compact_quote(formals[date][-1])
                    if latest['id']==old['formal_latest']['id']:continue
                    result=dict(old,formal_latest=latest,latest_comparison=comparison(old['estimate'],latest,old['first_comparison'].get('basis_verified',False)),updated_at=self.now())
                else:
                    formal=formals.get(date,[])
                    first=formal[0] if formal else None
                    cutoff_at=min(stamp(first['observed_at']),stamp(first['published_at'])) if first and first.get('published_at') else stamp(first['observed_at']) if first else None
                    # No hindsight reconstruction from quotes first seen after
                    # the formal disclosure or from an intraday/lunch print.
                    candidates=([old['estimate']] if old else [])+closes.get(date,[])
                    candidates=[r for r in candidates if cutoff_at is None or stamp(r['observed_at'])<cutoff_at]
                    if not candidates:continue
                    selected=sorted(candidates,key=lambda r:r['observed_at'])[-1]
                    candidate=old['estimate'] if old and selected['id']==old['estimate']['id'] else compact_quote(selected)
                    if old and not first and old['estimate']['id']==candidate['id']:continue
                    result={'fund_id':fid,'date':date,'estimate':candidate,'status':'PENDING',
                        'captured_at':(old or {}).get('captured_at',self.now()),'updated_at':self.now(),
                        'formal_first':None,'formal_latest':None,'first_comparison':None,'latest_comparison':None}
                    if first:
                        prior=[r for r in formals.get(candidate.get('reference_date'),[]) if stamp(r['observed_at'])<=stamp(first['observed_at'])]
                        basis_verified=bool(prior and candidate.get('reference_nav') is not None and dec(prior[-1]['value'])==dec(candidate['reference_nav']))
                        latest=compact_quote(formal[-1]);first=compact_quote(first)
                        result.update(status='RESOLVED',formal_first=first,formal_latest=latest,
                            first_comparison=comparison(candidate,first,basis_verified),latest_comparison=comparison(candidate,latest,basis_verified),resolved_at=self.now())
                if old and digest({k:v for k,v in result.items() if k not in {'_version','updated_at'}})==digest({k:v for k,v in old.items() if k not in {'_version','updated_at'}}):continue
                Store.put(c,DEVIATION_KIND,fid+':'+date,result,old['_version'] if old else 0);changed+=1
            saved=[r for r in Store.docs(c,DEVIATION_KIND) if r['fund_id']==fid]
            for old in sorted(saved,key=lambda r:r['date'],reverse=True)[DEVIATION_LIMIT:]:
                c.execute('DELETE FROM documents WHERE kind=? AND id=?',(DEVIATION_KIND,fid+':'+old['date']))
        return {'changed':changed,'per_fund_limit':DEVIATION_LIMIT}

    def estimate_result_summary(self,c,fund_id):
        rows=sorted((r for r in Store.docs(c,DEVIATION_KIND) if r['fund_id']==fund_id),key=lambda r:r['date'],reverse=True)
        done=[r for r in rows if r['status']=='RESOLVED']
        errors=[abs(dec(r['first_comparison']['change_error_pp'],nonnegative=False)) for r in done if r['first_comparison']['change_error_pp'] is not None]
        return {'rows':rows,'resolved':len(done),'pending':len(rows)-len(done),
            'mae_pp':(sum(errors,ZERO)/len(errors)).quantize(Decimal('.000000000001'),rounding=ROUND_HALF_UP) if errors else None,'scored_count':len(errors),
            'notice':'只核对首次取得正式值之前已保存的收盘估算；已知真实发布时间则采用更早的发布时间。未知发布时间不补造，首次取得也不代表公开发布时刻。误差为估算涨跌−同基准正式涨跌（百分点），不代表未来精度。首次结果不覆盖，正式修订另列；最多保留30个日期。'}

    def valuation_explanation(self,fund_id=None):
        with self.store.connect() as c:
            c.execute('BEGIN')
            snapshot=self.snapshot(c)
            if not fund_id:
                return explain_portfolio(snapshot)
            fund=next((f for f in snapshot['funds'] if f['id']==fund_id),None)
            require(fund is not None,'FUND_NOT_FOUND','基金不存在')
            quote_id=(fund.get('return_valuation') or {}).get('quote_id')
            row=c.execute('SELECT payload FROM observations WHERE id=?',(quote_id,)).fetchone()
            quote=dict(json.loads(row['payload']),id=quote_id) if row else None
            result=dict(explain_fund(fund,quote),scope='FUND',generated_at=snapshot['now'],revision=snapshot['revision'])
            state=self.state(c);audit=state['audit'];by_id={r['id']:r for r in audit}
            def belongs(event):
                p=event['payload'];order=state['orders'].get(p.get('order_id'),{})
                target=by_id.get(p.get('event_id'),{})
                return p.get('fund_id')==fund_id or order.get('fund_id')==fund_id or (target and target is not event and belongs(target))
            events=[e for e in audit if belongs(e)]
            # Event amounts remain evidence, not an invented daily attribution.
            result['recent_events']=[{k:e.get(k) for k in ('id','kind','occurred_at','recorded_at','reversed','payload')} for e in events[-8:][::-1]]
            result['correction_count']=sum(e['kind']=='REVERSAL' for e in events)
            sales=(fund.get('position_buckets') or {}).get('sales') or []
            result['recorded_sale_fees']=(sum((dec(s['fee']) for s in sales),ZERO) if all(s.get('fee') is not None for s in sales) else None)
            result['fee_scope']='已确认或已接受历史还原的卖出费用；是收益组成的说明，不再从累计收益扣一次。买入费用随原成本保留。'
            result['estimate_results']=self.estimate_result_summary(c,fund_id)
            return result
