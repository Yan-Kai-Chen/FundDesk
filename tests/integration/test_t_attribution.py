"""Anonymous scenarios for T attribution; these are not the user's trades."""
from copy import deepcopy
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.domain.finance import Problem
from app.main import create_app
from app.persistence.store import Store
from app.services.t_attribution import attribute, infer_units, reconstruct


def seed(w):
    with w.store.connect() as c:
        rule = w.rules(c, w.fid)
    w.save_rules(w.fid, dict(rule, expected_version=rule['_version'], purchase_rate='0',
        redemption_tiers=[{'min_days':0,'max_days':7,'rate':'0.015'},
            {'min_days':7,'max_days':30,'rate':'0.005'},
            {'min_days':30,'max_days':None,'rate':'0'}]))
    records = [('2026-08-01','BUY','CNY','120'), ('2026-09-01','BUY','CNY','80'),
               ('2026-09-05','SELL','FUND_UNIT','100')]
    p = {'fund_id':w.fid,'channel':'手工记录','history_complete':True,
         'sources':[{'sha256':'c'*64,'filename':'anonymous.jpg'}],
         'rows':[{'displayed_at':d+' 14:00:00','side':side,'unit':unit,'value':value,
                  'status':'PENDING_SETTLEMENT' if side=='SELL' else 'SHOWN',
                  'source_refs':[{'sha256':'c'*64,'row':i+1}]}
                 for i,(d,side,unit,value) in enumerate(records)]}
    w.save_statement(p)
    for d,v in [('2026-08-01','1.2'),('2026-09-01','0.8'),('2026-09-05','0.9'),('2026-09-10','0.95')]:
        w.observe({'fund_id':w.fid,'value_kind':'OFFICIAL_NAV','effective_date':d,'value':v,'source':'anonymous test'})
    return p


def analysis(w, method='RECENT_BUY_FIRST', **extra):
    return w.preview_t_analysis(w.fid, dict(method=method, **extra))


def save(w, a, **extra):
    return w.save_t_pairings(w.fid, dict(method=a['method'],pairs=a['pairs'],
        statement_version=a['statement_version'],rule_version=a['rule_version'],
        expected_version=a['pairing_version'],**extra))


def test_recent_pairing_explains_fifo_loss_without_changing_total_or_fees(wb):
    seed(wb)
    a = analysis(wb)
    # FIFO physically redeems the older, expensive lot held >30 days. The
    # recent economic T match is only 3 days old and must not change that fee.
    assert a['fifo_realized']==D('-30') and a['fifo_unrealized']==D('15')
    assert a['t_pnl']==D('10') and a['residual_pnl']==D('-25')
    assert a['total_pnl']==a['t_pnl']+a['residual_pnl']==D('-15')
    assert a['sell_details'][0]['fee']==0
    assert a['sell_details'][0]['pairs'][0]['buy_at'].startswith('2026-09-01')
    assert a['all_sales_paired'] and a['pending_sell_count']==1
    assert not a['pairing_saved']
    s=wb.snapshot()
    assert s['revision']==0 and not s['lots'] and not s['orders']
    assert s['portfolio']['equity'] is None


def test_reverse_t_waits_for_buyback_and_never_reuses_it(wb):
    p=seed(wb)
    a=analysis(wb,'NEXT_BUY_FIRST')
    assert a['t_pnl'] is None and a['sell_details'][0]['t_pnl'] is None
    assert not a['all_sales_paired'] and a['residual_pnl']==a['total_pnl']
    p['rows'] += [dict(p['rows'][1],displayed_at='2026-09-08 14:00:00'),
                 dict(p['rows'][2],displayed_at='2026-09-09 14:00:00',value='20')]
    p['expected_version']=1
    wb.save_statement(p)
    for d,v in [('2026-09-08','0.8'),('2026-09-09','1')]:
        wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':d,'value':v})
    a=analysis(wb,'NEXT_BUY_FIRST')
    assert a['paired_sales_count']==1 and a['sell_count']==2
    assert a['sell_details'][0]['pairs'][0]['direction']=='SELL_THEN_BUY'
    assert a['sell_details'][1]['t_pnl'] is None
    assert a['t_pnl']+a['residual_pnl']==a['total_pnl']
    assert a['residual_label']=='其余仓位及未配对交易盈亏'


def test_partial_manual_pairing_none_and_rounding_conserve_money(wb):
    seed(wb)
    ref=analysis(wb)
    sell_id=ref['sells'][0]['id']
    pairs=[{'buy_id':b['id'],'sell_id':sell_id,'units':'50'} for b in ref['buys']]
    a=analysis(wb,'MANUAL',pairs=pairs)
    assert a['t_pnl']==D('-10') and a['residual_pnl']==D('-5')
    assert sum(p['sell_net'] for p in a['sell_details'][0]['pairs'])==D('90')
    partial=analysis(wb,'MANUAL',pairs=[dict(pairs[0],units='33.33')])
    assert not partial['all_sales_paired'] and partial['t_pnl']+partial['residual_pnl']==partial['total_pnl']
    none=analysis(wb,'NONE')
    assert none['t_pnl'] is None and none['residual_pnl']==D('-15')
    # Separate penny-valued synthetic reference: final allocation gets the
    # remainder, so splitting one sell across two buys cannot lose a cent.
    synthetic=dict(ref,buys=[dict(ref['buys'][0],id='a',units=D('1'),cost=D('0.33')),
                            dict(ref['buys'][1],id='b',units=D('2'),cost=D('0.67'))],
                   sells=[dict(ref['sells'][0],id='s',units=D('3'),net=D('1.01'),fee=D('0.01'))])
    a=attribute(synthetic,'MANUAL',[{'buy_id':'a','sell_id':'s','units':'1'},
                                   {'buy_id':'b','sell_id':'s','units':'2'}])
    detail=a['sell_details'][0]
    assert detail['paired_net']==D('1.01') and detail['remaining_net']==0
    assert sum(p['sell_fee'] for p in detail['pairs'])==D('0.01')
    assert a['t_pnl']==D('0.01')


@pytest.mark.parametrize('mutation,code',[
    ('overbuy','PAIRING_OVERALLOCATED'),('oversell','PAIRING_OVERALLOCATED'),
    ('duplicate','PAIRING_DUPLICATE'),('unknown','PAIRING_SOURCE_MISSING'),
    ('precision','PAIRING_PRECISION'),('zero','INVALID_AMOUNT'),
    ('malformed','INVALID_T_PAIRING'),('missing','INVALID_DECIMAL'),
    ('source_type','PAIRING_SOURCE_MISSING')])
def test_bad_manual_pairings_are_rejected_without_ledger_writes(wb,mutation,code):
    seed(wb);a=analysis(wb)
    p={'buy_id':a['buys'][0]['id'],'sell_id':a['sells'][0]['id'],'units':'50'}
    pairs=[p]
    if mutation=='overbuy': p['units']='100.01'
    if mutation=='oversell': pairs.append(dict(p,buy_id=a['buys'][1]['id'],units='51'))
    if mutation=='duplicate': pairs.append(dict(p))
    if mutation=='unknown': p['buy_id']='unknown'
    if mutation=='precision': p['units']='0.001'
    if mutation=='zero': p['units']='0'
    if mutation=='malformed': pairs=[None]
    if mutation=='missing': del p['units']
    if mutation=='source_type': p['buy_id']=[]
    with pytest.raises(Problem) as e:analysis(wb,'MANUAL',pairs=pairs)
    assert e.value.code==code
    assert wb.snapshot()['revision']==0


def test_reconstruction_requires_history_nav_cutoff_and_rules(wb):
    p=seed(wb)
    with wb.store.connect() as c:
        s=Store.doc(c,'statement',wb.fid);h=wb.observations(c,wb.fid,'OFFICIAL_NAV');rule=wb.rules(c,wb.fid)
    scenarios=[(dict(s,history_complete=False),h,rule,'HISTORY_INCOMPLETE'),
               (s,[],rule,'NAV_MISSING'),(s,h,dict(rule,verified=False),'RULES_UNVERIFIED'),
               (s,h,dict(rule,unit_step='0.001'),'REFERENCE_STEP_UNSUPPORTED'),
               (s,h[:2],rule,'NAV_NOT_COVERED'),
               (s,[q for q in h if q['effective_date']!='2026-09-01'],rule,'NAV_MISSING'),
               (s,h,dict(rule,effective_from='2026-09-01'),'REFERENCE_RULE_DATE')]
    late=deepcopy(s);late['rows'][0]['displayed_at']='2026-08-01 15:00:00'
    scenarios.append((late,h,rule,'REFERENCE_CUTOFF'))
    for s_,h_,r_,code in scenarios:
        with pytest.raises(Problem) as e:reconstruct(s_,h_,r_)
        assert e.value.code==code
    p['history_complete']=False;p['expected_version']=1;wb.save_statement(p)
    assert wb.snapshot()['funds'][0]['statement']['t_analysis']['status']=='BLOCKED'


def test_currency_sell_inference_reports_ambiguity_or_unreachable(wb):
    seed(wb)
    lot={'id':'old','units':D('50'),'cost':D('20'),'acquired':'2026-01-01','available_from':'2026-01-02'}
    with wb.store.connect() as c:rule=wb.rules(c,wb.fid)
    p=infer_units([lot],D('4'),D('0.4'),rule,'2026-09-05')
    assert p['units']==D('9.99') and p['alternative_units']==D('10.00')
    with pytest.raises(Problem,match='无法'):infer_units([lot],D('21'),D('0.4'),rule,'2026-09-05')
    with pytest.raises(Problem):infer_units([],D('1'),D('0.4'),rule,'2026-09-05')
    with wb.store.connect() as c:
        s=Store.doc(c,'statement',wb.fid);h=wb.observations(c,wb.fid,'OFFICIAL_NAV')
    s['rows'][-1].update(unit='CNY',value='90')
    a=reconstruct(s,h,rule)
    assert a['sells'][0]['units']==D('100') and a['total_pnl']==D('-15')


def test_pairings_are_versioned_idempotent_backed_up_and_stale_basis_blocks(wb):
    p=seed(wb);a=analysis(wb)
    first=save(wb,a)
    assert first['_version']==1 and save(wb,a)['duplicate']
    snapshot=wb.snapshot()
    assert snapshot['revision']==0 and snapshot['funds'][0]['statement']['t_analysis']['pairing_saved']
    backup=wb.create_backup()
    none=analysis(wb,'NONE');save(wb,none)
    with pytest.raises(Problem,match='修改'):save(wb,a)
    wb.restore(backup['id'],backup['sha256'],True)
    restored=wb.snapshot()['funds'][0]['statement']['t_analysis']
    assert restored['method']=='RECENT_BUY_FIRST' and restored['t_pnl']==D('10')
    p['history_complete']=True;p['history_completeness_basis']='updated evidence';p['expected_version']=1
    wb.save_statement(p)
    assert wb.snapshot()['funds'][0]['statement']['t_analysis']['code']=='PAIRING_BASIS_CHANGED'
    with pytest.raises(Problem,match='重新预览'):save(wb,a)
    fresh=analysis(wb);save(wb,fresh)
    with wb.store.connect() as c:
        assert len(Store.docs(c,'t_pairing_history'))==2
        r=wb.rules(c,wb.fid)
    wb.save_rules(wb.fid,dict(r,expected_version=r['_version'],source='updated rule evidence'))
    assert wb.snapshot()['funds'][0]['statement']['t_analysis']['code']=='PAIRING_BASIS_CHANGED'


def test_corporate_actions_block_conditional_attribution(wb):
    seed(wb)
    with wb.store.transaction() as c:
        Store.put(c,'nav_sync',wb.fid,{'corporate_actions':[{'effective_date':'2026-09-01'}]})
    assert wb.snapshot()['funds'][0]['statement']['t_analysis']['code']=='CORPORATE_ACTION_REVIEW'
    with pytest.raises(Problem):analysis(wb)


def test_api_analysis_and_pairing_routes_require_session_and_csrf(wb):
    seed(wb)
    app=create_app(wb.root,testing=True)
    app.state.workbenches['real'].now=wb.now
    path=f'/api/v1/funds/{wb.fid}'
    with TestClient(app) as c:
        assert c.post(path+'/t-analysis',json={}).status_code==401
        csrf=c.get('/api/v1/session').json()['csrf_token']
        assert c.put(path+'/t-pairings',json={}).status_code==403
        c.headers['x-csrf-token']=csrf
        a=c.post(path+'/t-analysis',json={'method':'RECENT_BUY_FIRST'}).json()
        assert a['t_pnl']=='10.00' and a['status']=='REFERENCE'
        invalid=c.post(path+'/t-analysis',json={'method':[]})
        assert invalid.status_code==400 and invalid.json()['code']=='INVALID_PAIRING_METHOD'
        body={k:a[k] for k in ['method','pairs','statement_version','rule_version']}
        body['expected_version']=a['pairing_version']
        assert c.put(path+'/t-pairings',json=body).status_code==200
        s=c.get('/api/v1/state?mode=real').json()
        assert s['revision']==0 and s['funds'][0]['statement']['t_analysis']['pairing_saved']
