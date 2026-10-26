from decimal import Decimal as D
from datetime import date,timedelta
import pytest
from app.domain.finance import Problem
from app.persistence.store import Store
from conftest import event,deposit,opening,order,AT
from conftest import confirm


def history(w,count=30):
    start=date(2026,10,19)-timedelta(days=count-1)
    for i in range(count):
        d=(start+timedelta(days=i)).isoformat()
        w.observe({'fund_id':w.fid,'value_kind':'OFFICIAL_NAV','value':str(D('1')+D(i)/1000),'effective_date':d,'source':'合成测试历史'})


def test_import_preview_does_not_write_and_commit_is_atomic_idempotent(wb):
    content='external_event_id,event_type,amount_cny,effective_at\ncash-001,DEPOSIT,1000,2026-10-20T12:00:00+08:00\n'
    p=wb.import_preview('cash_events',content)
    assert not p['errors'] and wb.snapshot()['portfolio']['cash']==0
    wb.import_commit(p['id'],p['source_hash'])
    wb.import_commit(p['id'],p['source_hash'])
    assert wb.snapshot()['portfolio']['cash']==D('1000')


def test_invalid_import_rows_and_stale_preview_do_not_write(wb):
    content='event_type,amount_cny,effective_at\nDEPOSIT,100,2026-10-20T12:00:00+08:00\nDEPOSIT,invalid,2026-10-20T12:00:00+08:00\n'
    p=wb.import_preview('cash_events',content)
    assert p['errors'][0]['line']==3 and wb.snapshot()['revision']==0
    with pytest.raises(Problem):wb.import_commit(p['id'],p['source_hash'])
    p=wb.import_preview('cash_events','event_type,amount_cny,effective_at\nDEPOSIT,100,2026-10-20T12:00:00+08:00\n')
    deposit(wb)
    with pytest.raises(Problem):wb.import_commit(p['id'],p['source_hash'])


def test_import_preserves_leading_zero_and_unknown_fields(wb):
    p=wb.import_preview('funds','fund_code,name,share_class,channel\n000002,合成导入基金,C,手工记录\n')
    assert not p['errors'];wb.import_commit(p['id'],p['source_hash'])
    assert '000002' in [f['fund_code'] for f in wb.snapshot()['funds']]
    content='fund_code,lot_external_id,confirmed_units,remaining_cost_cny,purchase_confirm_date,available_from,snapshot_date\n000001,unknown-lot,100,,,,2026-10-19\n'
    p=wb.import_preview('lots',content);assert not p['errors'];wb.import_commit(p['id'],p['source_hash'])
    assert wb.snapshot()['lots'][0]['cost'] is None


def test_repeated_observation_preserves_first_seen_and_revisions(wb):
    p={'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','value':'1','effective_date':'2026-10-19','source':'test'}
    first=wb.observe(p)
    wb.now=lambda:'2026-10-20T06:00:00+00:00'
    duplicate=wb.observe(p)
    assert first['id']==duplicate['id']
    wb.observe(dict(p,value='1.01'))
    with wb.store.connect() as c:
        history_=wb.observations(c,wb.fid,'OFFICIAL_NAV',False)
        assert len(history_)==2 and history_[0]['observed_at']=='2026-10-20T05:30:00+00:00'
        assert len(wb.observations(c,wb.fid,'OFFICIAL_NAV'))==1


def test_manual_history_does_not_fabricate_time_evidence(wb):
    history(wb)
    result=wb.evaluate_prediction(wb.fid)
    assert result['status']=='INSUFFICIENT_TIME_EVIDENCE' and result['fold_count']==0 and result['mae'] is None


def test_prediction_append_only_match_later_truth_and_input_hash(wb):
    history(wb)
    p=wb.predict({'fund_id':wb.fid,'method':'LAST_VALUE','target_date':'2026-10-21','key':'prediction-test-key'})
    frozen=p['input_hash']
    wb.now=lambda:'2026-10-21T05:30:00+00:00'
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','value':'1.04','effective_date':'2026-10-21'})
    stored=wb.prediction_list()[0]
    assert stored['input_hash']==frozen and stored['predicted_nav']=='1.029'
    assert stored['resolution']['actual']['value']=='1.04'
    assert len(stored['input_ids'])==30


def test_insufficient_or_future_input_prediction_is_blocked(wb):
    with pytest.raises(Problem):wb.predict({'fund_id':wb.fid,'target_date':'2026-10-21','key':'no-data-prediction'})
    history(wb)
    with pytest.raises(Problem):wb.predict({'fund_id':wb.fid,'target_date':'2026-10-21','key':'bad-time-prediction','generated_at':'2026-08-02T00:00:00Z'})


def test_backup_roundtrip_integrity_mode_guard_and_tamper(wb):
    deposit(wb);history(wb)
    prediction=wb.predict({'fund_id':wb.fid,'target_date':'2026-10-21','key':'backup-prediction'})
    saved=wb.create_backup()
    deposit(wb,'200')
    preview=wb.preview_restore(saved['id'])
    assert preview['cash']==D('10000') and preview['counts']['predictions']==1
    restored=wb.restore(saved['id'],saved['sha256'],True)
    assert restored['safety_backup'] and wb.snapshot()['portfolio']['cash']==D('10000')
    assert wb.prediction_list()[0]['id']==prediction['id']
    db=wb.backup_path(saved['id'])/'workbench.sqlite3';db.write_bytes(db.read_bytes()+b'tamper')
    with pytest.raises(Problem):wb.preview_restore(saved['id'])
    with pytest.raises(Problem):wb.preview_restore('../../escape')


def test_strategy_budget_snapshot_determinism_and_cutoff(wb):
    deposit(wb,'1000')
    funds=[wb.fid]
    second=wb.save_fund({'fund_code':'000002','name':'第二合成测试基金','share_class':'A'})['id'];funds.append(second)
    with wb.store.connect() as c:rule=wb.rules(c,wb.fid)
    rule.pop('_version');wb.save_rules(second,rule)
    for fid in funds:
        wb.observe({'fund_id':fid,'value_kind':'OFFICIAL_NAV','value':'1','effective_date':'2026-10-19'})
        wb.save_strategy(fid,{'enabled':True,'type':'TARGET_WEIGHT','params':{'target_weight':'1','band':'0','single_amount':'800','daily_limit':'1000','cash_reserve':'0','max_value':'10000','min_value':'0','min_order':'1','max_fee_rate':'1'}})
    one=wb.recommendations();two=wb.recommendations()
    assert one['id']==two['id']
    assert sum(D(str(i['amount'])) for i in one['items'] if i['action']=='BUY')==D('1000')
    assert wb.snapshot()['portfolio']['available_cash']==D('1000')
    wb.now=lambda:'2026-10-20T07:01:00+00:00'
    assert all(i['action']=='WAIT' for i in wb.recommendations()['items'])


def test_rules_unknown_and_stale_data_explain_block(wb):
    deposit(wb)
    params={'target_weight':'1','band':'0','single_amount':'100','daily_limit':'1000','cash_reserve':'0','max_value':'10000','min_value':'0','min_order':'1'}
    wb.save_strategy(wb.fid,{'enabled':True,'type':'TARGET_WEIGHT','params':params})
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','value':'1','effective_date':'2026-08-01'})
    item=wb.recommendations()['items'][0]
    assert item['action']=='BLOCKED' and 'DATA_STALE' in item['blocked_by']


def test_expected_version_conflict(wb):
    deposit(wb)
    with pytest.raises(Problem):wb.event('CASH_DEPOSIT',{'amount':'100'},'stale-version-key',AT,expected=0)
    assert wb.snapshot()['portfolio']['cash']==D('10000')


def test_gram_import_is_not_silently_mapped_to_units(wb):
    p=wb.import_preview('funds','fund_code,name,quantity_unit\n000003,合成黄金测试,GRAM\n')
    assert p['errors'] and p['errors'][0]['code']=='UNIT_NOT_SUPPORTED'


def test_known_corporate_action_blocks_unverified_prediction_series(wb):
    history(wb);opening(wb)
    event(wb,'SPLIT',{'fund_id':wb.fid,'factor':'2'})
    with pytest.raises(Problem,match='复权'):wb.predict({'fund_id':wb.fid,'target_date':'2026-10-21','key':'post-split-test'})


def test_duplicate_external_order_with_different_amount_is_error(wb):
    deposit(wb)
    base='external_order_id,fund_code,side,submitted_at,requested_amount_cny\nSAME,000001,BUY,2026-10-20T12:00:00+08:00,'
    p=wb.import_preview('orders',base+'100\n');assert not p['errors'];wb.import_commit(p['id'],p['source_hash'])
    p=wb.import_preview('orders',base+'200\n');assert p['errors'][0]['code']=='DUPLICATE_EXTERNAL_ORDER'


def test_anchor_updates_only_after_final_actual_confirmation(wb):
    opening(wb)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','value':'1.1','effective_date':'2026-10-19'})
    params={'anchor_nav':'1','buy_trigger':'0.02','sell_trigger':'0.02','single_amount':'110','daily_limit':'2000','cash_reserve':'0','max_value':'10000','min_value':'0','min_order':'1','max_fee_rate':'1'}
    wb.save_strategy(wb.fid,{'enabled':True,'type':'ANCHORED_THRESHOLD','params':params})
    first=wb.recommendations()['items'][0]
    assert first['action']=='SELL' and first['anchor_nav']==D('1')
    oid=order(wb,'SELL','100',strategy_id=wb.fid)
    confirm(wb,oid,'50','1.05','52.50','0','52.50',False)
    assert wb.recommendations()['items'][0]['action']=='WAIT'
    confirm(wb,oid,'50','1.15','57.50','0','57.50',True)
    after=wb.recommendations()['items'][0]
    assert after['anchor_nav']==D('1.10') and after['action']=='HOLD'


def test_gold_group_budget_counts_all_proposals_and_pending_orders(wb):
    deposit(wb,'1000')
    ids=[]
    with wb.store.connect() as c:rule=wb.rules(c,wb.fid)
    rule.pop('_version')
    for code in ['000010','000011']:
        fid=wb.save_fund({'fund_code':code,'name':'合成黄金'+code,'instrument_type':'GOLD_LINKED'})['id'];ids.append(fid)
        wb.save_rules(fid,rule)
        wb.observe({'fund_id':fid,'value_kind':'OFFICIAL_NAV','value':'1','effective_date':'2026-10-19'})
        wb.save_strategy(fid,{'enabled':True,'type':'TARGET_WEIGHT','params':{'target_weight':'1','band':'0','single_amount':'800','daily_limit':'1000','cash_reserve':'0','max_value':'10000','min_value':'0','min_order':'1','group_limit':'500'}})
    items=wb.recommendations()['items']
    assert sum(D(str(i['amount'])) for i in items if i['action']=='BUY')==D('500')
    wb.order({'fund_id':ids[0],'side':'BUY','requested':'200','key':'gold-pending-order','occurred_at':AT})
    assert sum(D(str(i['amount'])) for i in wb.recommendations()['items'] if i['action']=='BUY')==D('300')


def test_csv_order_payment_confirmation_with_day_precision(wb):
    deposit(wb,'1000')
    p=wb.import_preview('orders','external_order_id,fund_code,side,submitted_at,requested_amount_cny\nFLOW,000001,BUY,2026-10-20T13:00:00+08:00,100\n')
    assert not p['errors'];wb.import_commit(p['id'],p['source_hash'])
    p=wb.import_preview('cash_events','external_event_id,event_type,amount_cny,effective_at,related_order_id\nFLOW-PAID,PAID,100,2026-10-20T13:01:00+08:00,FLOW\n')
    assert not p['errors'];wb.import_commit(p['id'],p['source_hash'])
    content='external_order_id,confirmation_external_id,event_type,nav_date,confirm_date,available_from,confirmed_nav,units_delta,gross_amount_delta_cny,fee_delta_cny,net_amount_delta_cny,is_final_for_order\nFLOW,FLOW-CONF,CONFIRMED,2026-10-19,2026-10-20,2026-10-21,1,99.85,100,0.15,99.85,true\n'
    p=wb.import_preview('confirmations',content)
    assert not p['errors'],p['errors'];wb.import_commit(p['id'],p['source_hash'])
    assert wb.snapshot()['orders'][0]['status']=='CONFIRMED'
    assert wb.snapshot()['audit'][0]['payload']['time_precision']=='DAY'


def test_csv_explicit_paid_flag_is_honoured(wb):
    deposit(wb,'1000')
    content='external_order_id,fund_code,side,submitted_at,requested_amount_cny,payment_state,cash_source\nPAID-ROW,000001,BUY,2026-10-20T13:00:00+08:00,100,PAID,TRACKED\n'
    p=wb.import_preview('orders',content);assert not p['errors'];wb.import_commit(p['id'],p['source_hash'])
    assert wb.snapshot()['portfolio']['cash']==D('900') and wb.snapshot()['portfolio']['pending_buy']==D('100')


def test_prediction_horizon_staleness_and_retry_content(wb):
    history(wb)
    p={'fund_id':wb.fid,'target_date':'2026-10-21','key':'horizon-prediction','method':'MEAN_RETURN_5'}
    result=wb.predict(p)
    assert result['horizon_steps']==2 and result['version']=='baseline-v1.1'
    assert wb.predict(p)['id']==result['id']
    with pytest.raises(Problem):wb.predict(dict(p,method='LAST_VALUE'))
    wb.now=lambda:'2026-11-10T05:30:00+00:00'
    with pytest.raises(Problem,match='过期'):wb.predict(dict(p,key='stale-prediction',target_date='2026-11-11'))


def test_revised_history_is_not_available_before_first_seen(wb):
    from app.providers.demo import seed
    from app.services.workbench import Workbench
    demo=Workbench(Store(wb.root/'data/demo/test.sqlite3','demo'),wb.root);seed(demo)
    before=demo.evaluate_prediction('demo-equity')['fold_count']
    demo.observe({'fund_id':'demo-equity','value_kind':'OFFICIAL_NAV','value':'1.0001','effective_date':'2026-09-01','source':'late synthetic revision'})
    after=demo.evaluate_prediction('demo-equity')['fold_count']
    assert after<before


def test_out_of_order_estimates_preserve_latest_market_time_and_all_points(wb):
    for time_,value in [('05:29','1.2'),('05:20','1.1')]:
        wb.observe({'fund_id':wb.fid,'value_kind':'THIRD_PARTY_ESTIMATE','value':value,'effective_date':'2026-10-20','as_of':f'2026-10-20T{time_}:00+00:00','source':'synthetic estimate'})
    fund=wb.snapshot()['funds'][0]
    assert fund['estimate']['value']=='1.2' and len(fund['estimate_history'])==2


def test_future_quote_timestamp_rejected(wb):
    with pytest.raises(Problem,match='行情本身时点'):
        wb.observe({'fund_id':wb.fid,'value_kind':'THIRD_PARTY_ESTIMATE','value':'1','effective_date':'2026-10-20','as_of':'2026-10-20T06:00:00Z'})


def test_actual_record_is_allowed_when_source_plan_has_expired(wb):
    from app.providers.demo import seed
    from app.services.workbench import Workbench
    demo=Workbench(Store(wb.root/'data/demo/test.sqlite3','demo'),wb.root);seed(demo)
    plan=demo.recommendations()
    demo.now=lambda:'2026-10-20T05:32:00+00:00'
    result=demo.order({'fund_id':'demo-equity','side':'BUY','requested':'500','key':'expired-plan-actual','occurred_at':demo.now(),'source_recommendation':plan['id'],'strategy_id':'demo-equity'})
    recorded=next(o for o in demo.snapshot()['orders'] if o['id']==result['order_id'])
    assert recorded['source_plan_stale'] and recorded['requested']==D('500')


def test_percentage_edit_roundtrip_uses_exact_decimal_strings(wb):
    p={'target_percent':'28','band_percent':'3','buy_percent':'2','sell_percent':'2','max_fee_percent':'0.5',
       'single_amount':'100','daily_limit':'1000','cash_reserve':'0','max_value':'10000','min_value':'0','min_order':'1'}
    saved=wb.save_strategy(wb.fid,{'type':'TARGET_WEIGHT','enabled':False,'params':p})
    snapshot=wb.snapshot()['funds'][0]['strategy']
    assert snapshot['params']['target_weight']=='0.28' and snapshot['display_percent']['target_percent']==D('28.00')
    p.update({k:str(v) for k,v in snapshot['display_percent'].items()})
    updated=wb.save_strategy(wb.fid,{'type':'TARGET_WEIGHT','enabled':False,'params':p,'expected_version':saved['_version']})
    assert updated['params']['target_weight']=='0.28'
