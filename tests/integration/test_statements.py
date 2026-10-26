from copy import deepcopy
from decimal import Decimal
import pytest
from app.domain.finance import Problem
from app.persistence.store import Store


def statement(w):
    return {'fund_id':w.fid,'channel':'手工记录','history_complete':True,
        'sources':[{'sha256':'a'*64,'filename':'synthetic.jpg','label':'合成测试资料'}],
        'rows':[{'displayed_at':'2026-09-01 14:00:00','side':'BUY','unit':'CNY','value':'100.00','status':'SHOWN',
                 'source_refs':[{'sha256':'a'*64,'row':1}]},
                {'displayed_at':'2026-09-02 14:00:00','side':'SELL','unit':'FUND_UNIT','value':'31.00','status':'PENDING_SETTLEMENT',
                 'expected_settlement_date':'2026-09-03','source_refs':[{'sha256':'a'*64,'row':2}]}]}


def test_statements_deduplicate_overlaps_and_exclude_closed_without_ledger_writes(wb):
    p=statement(wb)
    duplicate=deepcopy(p['rows'][0]);duplicate['source_refs'][0]['row']=4;duplicate['value']='100'
    closed=deepcopy(p['rows'][0]);closed.update(value='500.00',status='CLOSED')
    p['rows'] += [duplicate,closed]
    first=wb.save_statement(p)
    assert wb.save_statement(p)['duplicate']
    s=wb.snapshot()
    assert s['revision']==0 and not s['orders'] and not s['lots']
    fund=s['funds'][0]
    summary=fund['statement']['summary']
    assert summary['count']==2 and summary['duplicate_count']==1
    assert summary['buy_displayed_total']==Decimal('100')
    assert summary['pending_sell_units']==Decimal('31')
    assert s['portfolio']['equity'] is None and fund['initialization_pending']
    assert all(r['status']!='CLOSED' for r in fund['statement']['rows'])
    with wb.store.connect() as c:
        assert len(Store.docs(c,'statement_history'))==1


def test_statement_cannot_change_unit_or_conflicting_duplicate_silently(wb):
    p=statement(wb);p['rows'][0]['unit']='FUND_UNIT'
    with pytest.raises(Problem):wb.save_statement(p)
    p=statement(wb);p['rows'][0]['source_refs'][0]['sha256']='b'*64
    with pytest.raises(Problem):wb.save_statement(p)
    p=statement(wb);p['rows'].append(dict(p['rows'][0],status='PENDING_SETTLEMENT'))
    with pytest.raises(Problem):wb.save_statement(p)
    p=statement(wb);p['rows'][0]['displayed_at']='2027-01-01 14:00:00'
    with pytest.raises(Problem):wb.save_statement(p)
    assert wb.snapshot()['funds'][0]['statement'] is None


def test_statement_keeps_amount_and_pending_shares_distinct_and_backup_restores(wb):
    p=statement(wb)
    p['rows'].append(dict(p['rows'][1],displayed_at='2026-09-03 14:00:00',unit='CNY',value='20.12',status='SHOWN'))
    wb.save_statement(p)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-09-01','value':'1','source':'synthetic'})
    backup=wb.create_backup()
    p['rows'][0]['value']='110.00';p['expected_version']=1
    wb.save_statement(p)
    wb.restore(backup['id'],backup['sha256'],True)
    s=wb.snapshot()['funds'][0]['statement']
    assert s['summary']['buy_displayed_total']==Decimal('100')
    assert s['summary']['sell_displayed_amount']==Decimal('20.12')
    assert s['rows'][0]['same_date_nav']['value']=='1'


def test_public_nav_refresh_validates_scope_preserves_first_seen_and_failure_cache(wb,monkeypatch):
    f=wb.save_fund({'fund_code':'012183','share_class':'C','name':'广发沪港深精选混合C'})
    data={'records':[{'effective_date':'2026-10-19','value':'1.2','corporate_action':''}],
          'source':'合成公开源响应','source_url':'https://fund.eastmoney.com/pingzhongdata/012183.js','raw_sha256':'b'*64}
    monkeypatch.setattr('app.services.statements.fetch_public_nav',lambda *args:data)
    result=wb.refresh_public_nav(f['id'])
    assert result['inserted']==1 and wb.snapshot()['revision']==0
    with pytest.raises(Problem):wb.refresh_public_nav(f['id'])
    wb.now=lambda:'2026-10-20T05:40:00Z'
    assert wb.refresh_public_nav(f['id'])['inserted']==0
    with wb.store.connect() as c:
        row=wb.observations(c,f['id'])[0]
        assert row['observed_at']=='2026-10-20T05:30:00+00:00'
    def failed(*args):raise Problem('SOURCE_UNAVAILABLE','Synthetic failure')
    monkeypatch.setattr('app.services.statements.fetch_public_nav',failed)
    wb.now=lambda:'2026-10-20T05:50:00Z'
    with pytest.raises(Problem):wb.refresh_public_nav(f['id'])
    fund=next(x for x in wb.snapshot()['funds'] if x['id']==f['id'])
    assert fund['nav_sync']['status']=='FAILED' and fund['nav']['value']=='1.2'
    with pytest.raises(Problem):wb.refresh_public_nav(wb.fid)


def test_source_corporate_action_blocks_unreviewed_research(wb):
    with wb.store.transaction() as c:
        Store.put(c,'nav_sync',wb.fid,{'corporate_actions':[{'effective_date':'2026-09-01','corporate_action':'合成分红提示'}]})
    with pytest.raises(Problem) as exc:wb.evaluate_prediction(wb.fid)
    assert exc.value.code=='CORPORATE_ACTION_REVIEW'
