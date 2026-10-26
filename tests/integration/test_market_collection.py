from datetime import timedelta
from decimal import Decimal as D
import hashlib
import pytest

from app.domain.finance import Problem, stamp
from app.persistence.store import Store
from app.providers.public_market import parse_ths_estimate
from conftest import opening


def setup(w):
    with w.store.transaction() as c:
        f = Store.doc(c, "fund", w.fid)
        Store.put(c, "fund", w.fid, dict(f, fund_code="012183", share_class="C"), f["_version"])
    w.observe({"fund_id": w.fid, "value_kind": "OFFICIAL_NAV", "effective_date": "2026-10-19", "value": "1.2", "source": "synthetic"})


def test_two_verified_funds_collect_independently_with_correct_market_and_no_ledger_mutation(wb, monkeypatch):
    setup(wb)
    second = wb.save_fund({"fund_code": "021528", "name": "独立合成测试基金二", "share_class": "C"})
    other_class = wb.save_fund({"fund_code": "021528", "name": "独立合成未核实类别", "share_class": "A"})
    bases = {"012183": "1.2", "021528": "4"}
    nav_calls, quote_calls = [], []
    def nav(code, *a, **k):
        nav_calls.append(code)
        return {"source": "synthetic two-fund nav", "source_url": "https://example.invalid/" + code,
                "raw_sha256": hashlib.sha256(code.encode()).hexdigest(), "corporate_actions_complete": True,
                "records": [{"effective_date": "2026-10-19", "value": D(bases[code]), "corporate_action": ""}]}
    def quote(code, *a, **k):
        quote_calls.append(code)
        base = bases[code]
        value = str(D(base) * D("1.025"))
        body = f"vm_fd_{code}='2026-10-19;0930-1130,1300-1500|2026-10-20~{base}~1329,{value},{base},0'".encode()
        return dict(parse_ths_estimate(body, code, wb.now()), fallback_checked=False)
    monkeypatch.setattr('app.services.market_data.fetch_official_nav', nav)
    monkeypatch.setattr('app.services.market_data.fetch_estimate', quote)
    before = wb.snapshot()
    assert len(wb.collect_market_due()) == 2
    state = wb.snapshot(); funds = {f['id']: f for f in state['funds']}
    assert sorted(nav_calls) == sorted(quote_calls) == ['012183', '021528']
    for fid, scope in [(wb.fid, 'CN_HK'), (second['id'], 'CN')]:
        f = funds[fid]
        assert f['automatic_market_supported'] is True
        assert f['daily_t']['market']['scope'] == scope
        assert f['daily_t']['today']['status'] == 'READY'
        assert f['daily_t']['today']['change'] == D('.025')
        assert f['estimate']['provenance']['source_url'].find(f['fund_code']) >= 0
        assert f['daily_t']['profile']['level'] == 'OBSERVE'
    assert funds[other_class['id']]['automatic_market_supported'] is False
    assert funds[other_class['id']]['daily_t']['market']['scope'] == 'UNKNOWN'
    assert state['revision'] == before['revision'] and state['audit'] == before['audit']
    assert state['portfolio'] == before['portfolio']
    wb.collect_market_due()
    assert len(nav_calls) == len(quote_calls) == 2


def estimate(w, suffix="", base="1.2", minute="1329"):
    body = f"vm_fd_012183='2026-10-19;0930-1130,1300-1500|2026-10-20~{base}~{minute},1.23,{base},0'".encode() + suffix.encode()
    return dict(parse_ths_estimate(body, "012183", w.now()), fallback_checked=False)


def test_automatic_ingestion_is_idempotent_provenance_keeps_first_seen_and_no_financial_events(wb, monkeypatch):
    setup(wb);opening(wb)
    before = wb.snapshot()
    data = estimate(wb)
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: data)
    first = wb.refresh_intraday(wb.fid)
    assert first["inserted"] == 1
    after = wb.snapshot();f = after['funds'][0]
    # Both displayed returns and daily price impact are read-only projections;
    # the underlying formal holdings/P&L and financial events stay fixed.
    projections = {'day_valuation', 'return_valuation'}
    assert {k: v for k, v in after['portfolio'].items() if k not in projections} == {
        k: v for k, v in before['portfolio'].items() if k not in projections}
    assert (after['portfolio']['return_valuation']['pnl'] - before['portfolio']['pnl']) == D('30.00')
    assert after['revision'] == before['revision']
    assert before['portfolio']['day_valuation']['total'] is None
    assert after['portfolio']['day_valuation']['total'] == D('30.00')
    q = f['daily_t']['today']['quote']
    assert f['daily_t']['today']['change'] == D('.025')
    assert q['quality'] == 'PUBLIC_THIRD_PARTY_ESTIMATE' and q['provenance']['reference_date'] == '2026-10-19'
    original_receipt, original_seen = q['source_receipt_hash'], q['observed_at']
    wb.now = lambda: '2026-10-20T05:40:00Z'
    data = estimate(wb, suffix='\n')
    assert wb.refresh_intraday(wb.fid)['inserted'] == 0
    with wb.store.connect() as c:
        all_q = wb.observations(c, wb.fid, 'THIRD_PARTY_ESTIMATE', False)
    assert len(all_q) == 1 and all_q[0]['source_receipt_hash'] == original_receipt
    assert all_q[0]['observed_at'] == original_seen and all_q[0]['fetched_at'] != original_seen
    wb.now = lambda: '2026-10-20T05:46:00Z'
    assert wb.snapshot()['funds'][0]['daily_t']['today']['status'] == 'STALE'


def test_wrong_reference_rolls_back_quotes_and_backoff_survives(wb, monkeypatch):
    setup(wb)
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: estimate(wb, base='1.1'))
    with pytest.raises(Problem, match='基准'):
        wb.refresh_intraday(wb.fid)
    with wb.store.connect() as c:
        assert not wb.observations(c, wb.fid, 'THIRD_PARTY_ESTIMATE')
        state = Store.doc(c, 'intraday_sync', wb.fid)
    assert state['status'] == 'FAILED' and state['error_code'] == 'QUOTE_BASIS_MISMATCH'
    with pytest.raises(Problem, match='10 分钟'):
        wb.refresh_intraday(wb.fid)


def test_failure_preserves_last_quote_and_next_attempt_is_bounded(wb, monkeypatch):
    setup(wb)
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: estimate(wb))
    wb.refresh_intraday(wb.fid)
    wb.now = lambda: '2026-10-20T05:40:00Z'
    def fail(*a, **k):
        e = Problem('ESTIMATE_UNAVAILABLE', 'synthetic failure')
        e.fallback_checked = True
        e.provider_errors = [{'provider': 'synthetic', 'code': 'SOURCE_UNAVAILABLE'}]
        raise e
    monkeypatch.setattr('app.services.market_data.fetch_estimate', fail)
    for count in range(1, 7):
        with pytest.raises(Problem):
            wb.refresh_intraday(wb.fid)
        with wb.store.connect() as c:
            s = Store.doc(c, 'intraday_sync', wb.fid)
        assert s['failure_count'] == count
        assert (stamp(s['next_attempt_at'])-stamp(wb.now())).total_seconds() <= 900
        assert s['last_success_at'] == '2026-10-20T05:30:00+00:00'
        next_at = s['next_attempt_at']
        with pytest.raises(Problem, match='10 分钟'):
            wb.refresh_intraday(wb.fid)
        wb.now = lambda value=next_at: value
    f = wb.snapshot()['funds'][0]
    assert f['estimate']['as_of'] == '2026-10-20T05:29:00+00:00'
    assert f['intraday_sync']['next_fallback_at']


def nav_data(complete=True):
    return {'source':'synthetic auto NAV', 'source_url':'https://example.invalid/nav', 'raw_sha256':'a'*64,
            'corporate_actions_complete':complete,
            'records':[{'effective_date':'2026-10-19','value':D('1.2'),'corporate_action':''}]}


def test_collection_tick_collects_supported_real_only_and_10_minute_official_schedule(wb, monkeypatch):
    setup(wb)
    nav_calls, quote_calls = [], []
    monkeypatch.setattr('app.services.market_data.fetch_official_nav', lambda *a, **k: nav_calls.append(wb.now()) or nav_data())
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: quote_calls.append(wb.now()) or
                        estimate(wb, minute='1500' if stamp(wb.now()).hour >= 7 else '1329'))
    assert wb.collect_market_due()[0]['intraday']['status'] == 'READY'
    wb.collect_market_due()
    assert len(nav_calls) == 1 and len(quote_calls) == 1
    wb.now = lambda: '2026-10-20T08:00:00Z'  # 16:00 Beijing
    assert wb.collect_market_due()[0]['intraday']['status'] == 'READY'
    wb.now = lambda: '2026-10-20T08:09:59Z'
    assert wb.collect_market_due()[0]['intraday']['status'] == 'CLOSE_CACHED'
    assert len(nav_calls) == 2 and len(quote_calls) == 2
    wb.now = lambda: '2026-10-20T08:10:00Z'
    wb.collect_market_due()
    assert len(nav_calls) == 3
    # Today's first NAV does not stop checks for later corrections.
    with wb.store.transaction() as c:
        s = Store.doc(c, 'nav_sync', wb.fid)
        Store.put(c, 'nav_sync', wb.fid, dict(s, latest_date='2026-10-20'), s['_version'])
    wb.now = lambda: '2026-10-20T09:00:00Z'
    wb.collect_market_due()
    assert len(nav_calls) == 4


@pytest.mark.parametrize('at', ['2026-10-20T01:00:00Z', '2026-10-24T02:00:00Z'])
def test_no_intraday_network_before_open_or_weekend(wb, monkeypatch, at):
    setup(wb);wb.now = lambda: at
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: pytest.fail('unexpected network'))
    assert wb.refresh_intraday(wb.fid)['status'] == 'OUTSIDE_SESSION'


def test_lunch_start_fetches_morning_once_then_cached_manual_still_unlimited(wb, monkeypatch):
    wb.now = lambda: '2026-10-20T12:30:00+08:00'; setup(wb); before = wb.snapshot(); calls = []
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **kw: calls.append(kw) or estimate(wb, minute='1129'))
    assert wb.refresh_intraday(wb.fid)['status'] == 'READY'
    assert wb.refresh_intraday(wb.fid)['status'] == 'BREAK_CACHED'
    wb.now = lambda: '2026-10-20T12:50:00+08:00'
    assert wb.refresh_intraday(wb.fid)['status'] == 'BREAK_CACHED' and len(calls) == 1
    for _ in range(2): assert wb.refresh_intraday(wb.fid, manual=True)['inserted'] == 0
    assert len(calls) == 3
    after = wb.snapshot(); q = after['funds'][0]['daily_t']['today']
    assert q['status'] == 'READY' and q['phase'] == 'BREAK' and q['change'] == D('.025')
    assert before['revision'] == after['revision'] and before['portfolio'] == after['portfolio']
    wb.now = lambda: '2026-10-20T13:00:00+08:00'
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **kw: calls.append(kw) or estimate(wb, minute='1300'))
    assert wb.refresh_intraday(wb.fid)['status'] == 'READY' and len(calls) == 4
    assert wb.snapshot()['funds'][0]['daily_t']['today']['phase'] == 'LIVE'


def test_source_baseline_revision_blocks_old_estimate_and_missing_actions_block_t(wb, monkeypatch):
    setup(wb)
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: estimate(wb))
    wb.refresh_intraday(wb.fid)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','value':'1.21','effective_date':'2026-10-19','source':'synthetic revision'})
    assert wb.snapshot()['funds'][0]['daily_t']['today']['status'] == 'QUOTE_BASIS_MISMATCH'
    with wb.store.transaction() as c:
        Store.put(c, 'nav_sync', wb.fid, {'corporate_actions':[{'date':'synthetic'}]})
    wb.refresh_public_nav(wb.fid, fetcher=lambda *a: nav_data(complete=False))
    with wb.store.connect() as c:
        s = Store.doc(c, 'nav_sync', wb.fid)
    assert s['corporate_actions'] and s['corporate_actions_complete'] is False
    assert wb.snapshot()['funds'][0]['daily_t']['today']['status'] == 'CORPORATE_ACTION_REVIEW'


def test_demo_and_unverified_fund_do_not_fetch(wb, monkeypatch):
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: pytest.fail('unexpected network'))
    with pytest.raises(Problem):wb.refresh_market(wb.fid)
    wb.mode = 'demo'
    assert wb.collect_market_due() == []
    with pytest.raises(Problem):wb.refresh_intraday(wb.fid)


@pytest.mark.parametrize('code,scope', [('012738','CN'),('019633','CN'),('017954','CN_HK')])
def test_added_funds_use_independent_estimates_and_reference_markets(wb,monkeypatch,code,scope):
    f=wb.save_fund({'fund_code':code,'share_class':'C','name':'Synthetic imported alias C'})
    wb.observe({'fund_id':f['id'],'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-19','value':'2','source':'synthetic'})
    before=wb.snapshot()
    def quote(requested,*a,**kw):
        assert requested==code
        return dict(parse_ths_estimate(f"vm_fd_{code}='2026-10-19;0930-1130,1300-1500|2026-10-20~2~1329,2.04,2,0'".encode(),code,wb.now()),fallback_checked=False)
    monkeypatch.setattr('app.services.market_data.fetch_estimate',quote)
    wb.refresh_intraday(f['id'])
    state=wb.snapshot(); current=next(x for x in state['funds'] if x['id']==f['id'])
    assert current['automatic_market_supported'] and current['daily_t']['market']['scope']==scope
    assert current['daily_t']['today']['change']==D('.02')
    assert current['daily_t']['profile']['level']=='OBSERVE'
    assert state['revision']==before['revision'] and state['portfolio']==before['portfolio'] and state['audit']==before['audit']
    assert next(x for x in state['funds'] if x['id']==wb.fid)['estimate'] is None


def test_final_capture_persists_across_restart_and_manual_refresh_stays_unrestricted(wb,monkeypatch):
    from app.services.workbench import Workbench
    setup(wb); before=wb.snapshot(); calls=[]
    wb.now=lambda:'2026-10-20T15:25:00+08:00'
    monkeypatch.setattr('app.services.market_data.fetch_estimate',lambda *a,**kw:calls.append(kw) or estimate(wb,minute='1500'))
    assert wb.refresh_intraday(wb.fid)['inserted']==1
    reopened=Workbench(wb.store,wb.root);reopened.now=lambda:'2026-10-20T15:30:00+08:00'
    assert reopened.refresh_intraday(wb.fid)['status']=='CLOSE_CACHED' and len(calls)==1
    for _ in range(2):assert reopened.refresh_intraday(wb.fid,manual=True)['inserted']==0
    assert len(calls)==3
    after=reopened.snapshot(); current=next(f for f in after['funds'] if f['id']==wb.fid)
    assert current['daily_t']['today']['phase']=='CLOSE' and current['daily_t']['today']['change']==D('.025')
    assert after['revision']==before['revision'] and after['portfolio']==before['portfolio']
    reopened.now=lambda:'2026-10-20T23:00:00+08:00'
    assert reopened.refresh_intraday(wb.fid)['status']=='READY' and len(calls)==4
    reopened.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'1.24','source':'synthetic'})
    assert reopened.refresh_intraday(wb.fid)['status']=='OFFICIAL_AVAILABLE' and len(calls)==4
    assert reopened.snapshot()['funds'][0]['daily_t']['today']['phase']=='OFFICIAL'


def test_missing_final_point_retries_then_caches_and_revised_basis_invalidates_cache(wb,monkeypatch):
    setup(wb);wb.now=lambda:'2026-10-20T16:00:00+08:00'
    monkeypatch.setattr('app.services.market_data.fetch_estimate',lambda *a,**kw:estimate(wb,minute='1459'))
    with pytest.raises(Problem) as exc:wb.refresh_intraday(wb.fid)
    assert exc.value.code=='QUOTE_STALE'
    monkeypatch.setattr('app.services.market_data.fetch_estimate',lambda *a,**kw:estimate(wb,minute='1500'))
    wb.now=lambda:'2026-10-20T16:10:00+08:00'
    assert wb.refresh_intraday(wb.fid)['status']=='READY'
    assert wb.refresh_intraday(wb.fid)['status']=='CLOSE_CACHED'
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-19','value':'1.21','source':'synthetic revision'})
    with pytest.raises(Problem) as exc:wb.refresh_intraday(wb.fid)
    assert exc.value.code=='RATE_LIMITED'
    monkeypatch.setattr('app.services.market_data.fetch_estimate',lambda *a,**kw:estimate(wb,minute='1500',base='1.21'))
    wb.now=lambda:'2026-10-20T16:20:00+08:00'
    assert wb.refresh_intraday(wb.fid)['status']=='READY'


def test_post_close_updates_estimate_then_formal_and_later_formal_revision(wb,monkeypatch):
    setup(wb);wb.now=lambda:'2026-10-20T16:00:00+08:00'
    nav=nav_data();value='1.23'
    monkeypatch.setattr('app.services.market_data.fetch_official_nav',lambda *a,**kw:nav)
    def fetch(*a,**kw):
        data=f"vm_fd_012183='2026-10-19;0930-1130,1300-1500|2026-10-20~1.2~1500,{value},1.2,0'".encode()
        return parse_ths_estimate(data,'012183',wb.now())
    monkeypatch.setattr('app.services.market_data.fetch_estimate',fetch)
    wb.refresh_market(wb.fid)
    assert wb.snapshot()['funds'][0]['daily_t']['today']['change']==D('.025')
    wb.now=lambda:'2026-10-20T16:10:00+08:00';value='1.236'
    wb.refresh_market(wb.fid)
    assert wb.snapshot()['funds'][0]['daily_t']['today']['change']==D('.03')
    wb.now=lambda:'2026-10-20T16:20:00+08:00'
    nav['records'].append({'effective_date':'2026-10-20','value':D('1.248'),'corporate_action':''})
    assert wb.refresh_market(wb.fid)['intraday']['status']=='OFFICIAL_AVAILABLE'
    assert wb.snapshot()['funds'][0]['daily_t']['today']['change']==D('.04')
    wb.now=lambda:'2026-10-20T16:30:00+08:00';nav['records'][-1]['value']=D('1.26')
    wb.refresh_market(wb.fid)
    today=wb.snapshot()['funds'][0]['daily_t']['today']
    assert today['phase']=='OFFICIAL' and today['change']==D('.05') and wb.snapshot()['revision']==0


def test_intraday_waits_full_ten_minutes_but_manual_refresh_can_repeat_immediately(wb, monkeypatch):
    setup(wb)
    calls = []
    data = estimate(wb)
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: calls.append(k) or data)
    wb.refresh_intraday(wb.fid)
    wb.now = lambda: '2026-10-20T05:39:59Z'
    with pytest.raises(Problem) as exc: wb.refresh_intraday(wb.fid)
    assert exc.value.code == 'RATE_LIMITED' and len(calls) == 1
    wb.now = lambda: '2026-10-20T05:40:00Z'
    wb.refresh_intraday(wb.fid)
    for _ in range(3): wb.refresh_intraday(wb.fid, manual=True)
    assert len(calls) == 5
    with wb.store.connect() as c:
        s = Store.doc(c, 'intraday_sync', wb.fid)
    assert s['next_attempt_at'] == '2026-10-20T05:50:00+00:00'
    assert s['automatic_interval_seconds'] == 600


def test_manual_refresh_bypasses_failed_automatic_retry_and_fallback_cooldown(wb, monkeypatch):
    setup(wb)
    def fail(*a, **k):
        exc = Problem('RATE_LIMITED', 'synthetic upstream 429')
        exc.fallback_checked = True
        raise exc
    monkeypatch.setattr('app.services.market_data.fetch_estimate', fail)
    with pytest.raises(Problem): wb.refresh_intraday(wb.fid)
    with wb.store.connect() as c:
        s = Store.doc(c, 'intraday_sync', wb.fid)
    assert (stamp(s['next_attempt_at']) - stamp(wb.now())).total_seconds() == 600
    calls = []
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: calls.append(k) or estimate(wb))
    wb.refresh_intraday(wb.fid, manual=True)
    wb.refresh_intraday(wb.fid, manual=True)
    assert len(calls) == 2 and all(k['allow_fallback'] for k in calls)


def test_combined_manual_refresh_rechecks_both_sources_on_every_completed_click(wb, monkeypatch):
    setup(wb)
    nav_calls, quote_calls = [], []
    monkeypatch.setattr('app.services.market_data.fetch_official_nav', lambda *a, **k: nav_calls.append(1) or nav_data())
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: quote_calls.append(1) or estimate(wb))
    wb.collect_market_due()
    for _ in range(3):
        result = wb.refresh_market(wb.fid, manual=True)
        assert result['nav']['status'] == result['intraday']['status'] == 'READY'
    assert len(nav_calls) == len(quote_calls) == 4


@pytest.mark.parametrize('kind', ['intraday', 'nav'])
def test_manual_refresh_joins_no_parallel_request_but_does_not_cool_down_after_completion(wb, monkeypatch, kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    setup(wb)
    entered, release = Event(), Event()
    calls = []
    def slow(*a, **k):
        calls.append(1); entered.set()
        assert release.wait(3)
        return estimate(wb) if kind == 'intraday' else nav_data()
    if kind == 'intraday':
        monkeypatch.setattr('app.services.market_data.fetch_estimate', slow)
        refresh = lambda: wb.refresh_intraday(wb.fid, manual=True)
    else:
        refresh = lambda: wb.refresh_public_nav(wb.fid, fetcher=slow, manual=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(refresh)
        try:
            assert entered.wait(2)
            with pytest.raises(Problem) as exc: refresh()
            assert exc.value.code == 'COLLECTION_IN_PROGRESS'
            assert len(calls) == 1
        finally: release.set()
        task.result(timeout=2)
    refresh()
    assert len(calls) == 2


@pytest.mark.parametrize('manual', [False, True])
def test_abandoned_collection_checking_state_recovers_after_lease(wb, monkeypatch, manual):
    setup(wb)
    with wb.store.transaction() as c:
        Store.put(c, 'intraday_sync', wb.fid, {
            'status':'CHECKING', 'attempted_at':'2026-10-20T05:19:00Z',
            'next_attempt_at':'2026-10-20T05:29:00Z'})
    monkeypatch.setattr('app.services.market_data.fetch_estimate', lambda *a, **k: estimate(wb))
    assert wb.refresh_intraday(wb.fid, manual=manual)['status'] == 'READY'
