from datetime import datetime
from decimal import Decimal as D
import json
import httpx
import pytest

from app.domain.finance import Problem, SHANGHAI
from app.domain.market_calendar import market_session
from app.domain.daily_t import today_quote
from app.providers.gold_reference import (build_reference, parse_pm_fixings, parse_spot,
    fetch_gold_reference, historical_deviation, LIMIT)
from app.providers.public_nav import parse_public_nav
from app.domain.finance import dec
from test_public_nav import body as nav_body

NOW = '2026-10-21T13:30:00+08:00'


def fixings(today=False):
    rows = [['2026-10-19', 100], ['2026-10-20', 100]]
    if today: rows.append(['2026-10-21', 102])
    return json.dumps({'wp': [[int(datetime.fromisoformat(d).replace(tzinfo=SHANGHAI).timestamp()*1000), v]
                              for d, v in rows], 'zp': []}).encode()


def spot(**kw):
    data = {'heyue': 'Au99.99', 'delaystr': '2026年10月21日 13:29:55',
            'times': ['20:00', '02:00', '09:00', '13:29', '15:30'], 'data': ['888', '888', '100', '101', '999']}
    return json.dumps(dict(data, **kw)).encode()


def history():
    return [{'id': 'base', 'effective_date': '2026-10-20', 'value': '2', 'value_kind': 'OFFICIAL_NAV',
             'as_of': None, 'observed_at': '2026-10-20T20:00:00+08:00'}]


def test_live_proxy_uses_only_genuine_current_day_point_and_decimal_ratio():
    result = build_reference('008987', history(), fixings(), spot(), NOW)
    assert result['records'] == [{'value': D('2.0190'), 'effective_date': '2026-10-21', 'as_of': '2026-10-21T13:29:00+08:00'}]
    assert result['quality'] == 'LOCAL_GOLD_REFERENCE' and result['value_kind'] == 'LOCAL_ESTIMATE'
    e = result['evidence']
    assert e['gold_price'] == 101 and e['previous_pm_price'] == 100 and e['gold_unit'] == 'CNY_PER_GRAM'
    assert not e['fixed_for_date'] and e['reference_mode'] == 'LIVE_SPOT_PROXY'
    assert b'999' in result['raw_body']  # receipt preserved; future placeholder never used


def test_nonterminating_realistic_gold_ratio_meets_observation_precision_contract():
    source=json.loads(fixings());source['wp'][-1][1]=889.87
    result=build_reference('008987',[dict(history()[0],value='1.8621')],json.dumps(source).encode(),
                           spot(data=['888','888','885','886','999']),NOW)
    value=result['records'][0]['value']
    assert dec(value,positive=True)==D('1.85440673') and value.as_tuple().exponent==-8


@pytest.mark.parametrize('patch,code', [
    ({'heyue': 'Au(T+D)'}, 'SOURCE_IDENTITY_MISMATCH'),
    ({'delaystr': '2026年10月20日 13:29:55'}, 'QUOTE_WRONG_DATE'),
    ({'delaystr': '2026年10月21日 13:30:01'}, 'QUOTE_STALE'),
    ({'delaystr': '2026年10月21日 13:14:59'}, 'QUOTE_STALE'),
    ({'delaystr': 'unknown'}, 'SOURCE_FORMAT_CHANGED'),
    ({'times': ['13:29', '13:29']}, 'SOURCE_FORMAT_CHANGED'),
    ({'data': ['1']}, 'SOURCE_FORMAT_CHANGED'),
    ({'data': ['888', '888', '100', '0', '999']}, 'INVALID_AMOUNT'),
])
def test_wrong_contract_stale_future_malformed_and_zero_rejected(patch, code):
    with pytest.raises(Problem) as exc: parse_spot(spot(**patch), NOW)
    assert exc.value.code == code


def test_missing_day_point_and_old_point_rejected():
    with pytest.raises(Problem): parse_spot(spot(times=['20:00'], data=['100']), NOW)
    with pytest.raises(Problem): parse_spot(spot(times=['09:00'], data=['100']), NOW)


def test_gold_day_close_is_retained_without_using_future_or_night_placeholders():
    payload=spot(delaystr='2026年10月21日 15:30:05', data=['888','888','100','101','102'])
    now='2026-10-21T23:30:00+08:00'
    point=parse_spot(payload,now)
    assert point['closing_reference'] and point['value']==102 and point['as_of']=='2026-10-21T15:30:00+08:00'
    data=build_reference('008987',history(),fixings(),payload,now)
    assert data['evidence']['reference_mode']=='DAY_CLOSE_PROXY' and data['records'][0]['value']==D('2.0380')
    quote=dict(data['records'][0], id='close', value_kind=data['value_kind'], quality=data['quality'],
               observed_at='2026-10-21T16:00:00+08:00', provenance=dict(data['evidence'], provider_id=data['provider_id'],
                  reference_date=data['reference_date'],reference_nav=data['reference_nav']))
    current=today_quote(history(),[quote],now,market_session(now,'GOLD_CN'))
    assert current['phase']=='CLOSE' and current['change']==D('.019') and current['label']=='金价收盘参考估算'
    for p,at in [(payload,'2026-10-22T10:00:00+08:00'),
                 (spot(delaystr='2026年10月21日 15:29:55'),now),
                 (spot(delaystr='2026年10月21日 20:30:05'),now),
                 (spot(delaystr='2026年10月21日 15:30:05',times=['15:29'],data=['102']),now)]:
        with pytest.raises(Problem):parse_spot(p,at)


def test_today_pm_fixed_date_never_invents_publication_time_and_overrides_live():
    result = build_reference('008987', history(), fixings(True), b'invalid not needed', '2026-10-21T14:40:00+08:00')
    assert result['records'][0]['as_of'] is None and result['records'][0]['value'] == D('2.0380')
    assert result['evidence']['fixed_for_date'] and result['evidence']['source_clock'] is None
    assert json.loads(result['raw_body'])['spot'] is None
    with pytest.raises(Problem) as exc: build_reference('008987', history(), fixings(True), spot(), NOW)
    assert exc.value.code == 'QUOTE_FUTURE'


def test_fixed_quote_is_valid_for_that_date_only_and_formal_nav_wins():
    now = '2026-10-21T22:00:00+08:00'
    data = build_reference('008987', history(), fixings(True), None, '2026-10-21T15:10:00+08:00')
    quote = dict(data['records'][0], id='ref', value_kind=data['value_kind'], quality=data['quality'],
                 observed_at='2026-10-21T14:30:00+08:00', provenance=dict(data['evidence'],
                    provider_id=data['provider_id'], reference_date=data['reference_date'], reference_nav=data['reference_nav']))
    result = today_quote(history(), [quote], now, market_session(now, 'GOLD_CN'))
    assert result['status'] == 'READY' and result['change'] == D('.019') and result['label'] == '上海金午盘参考估算'
    formal = dict(history()[0], id='formal', effective_date='2026-10-21', value='2.04', observed_at='2026-10-21T21:00:00+08:00')
    assert today_quote(history()+[formal], [quote], now, market_session(now, 'GOLD_CN'))['quote']['id'] == 'formal'
    fake = dict(quote, quality='MANUAL')
    assert today_quote(history(), [fake], now, market_session(now, 'GOLD_CN'))['status'] == 'STALE'
    nextday = '2026-10-22T10:00:00+08:00'
    assert today_quote(history()+[formal], [quote], nextday, market_session(nextday, 'GOLD_CN'))['status'] == 'MISSING'


@pytest.mark.parametrize('at,status', [('08:59:59','NOT_OPEN'),('09:00:00','OPEN'),('12:00:00','OPEN'),
                                      ('15:29:59','OPEN'),('15:30:00','CLOSED')])
def test_sge_day_session_and_fund_cutoff_are_separate(at,status):
    s = market_session('2026-10-21T'+at+'+08:00', 'GOLD_CN')
    assert s['status'] == status and s['previous_date'] == '2026-10-20'
    assert s['before_cutoff'] == (at < '15:00:00')


def test_holiday_weekend_unknown_year_not_open():
    for on in ['2026-10-01','2026-10-10']:
        assert market_session(on+'T10:00:00+08:00','GOLD_CN')['status'] == 'NOT_OPEN'
    assert market_session('2027-01-04T10:00:00+08:00','GOLD_CN')['status'] == 'UNKNOWN'


def test_bad_fixing_series_baseline_and_unknown_fund_rejected():
    for payload in [b'{}', b'{"wp": [[1,1],[1,1]]}', fixings(True), b'x'*(LIMIT+1), b'{"wp": NaN}']:
        with pytest.raises(Problem): parse_pm_fixings(payload, '2026-10-20T13:30:00+08:00')
    for code, hist in [('008986',history()),('008987',[]),('008987',[dict(history()[0],effective_date='2026-10-19')])]:
        with pytest.raises(Problem): build_reference(code,hist,fixings(),spot(),NOW)


def test_provider_http_protocol_bounded_and_no_spot_fetch_after_pm():
    requests=[]
    def handler(req):
        requests.append(req)
        return httpx.Response(200,content=fixings(True))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r=fetch_gold_reference('008987',lambda:'2026-10-21T14:40:00+08:00',history(),client=client)
    assert len(requests)==1 and requests[0].method=='POST' and r['evidence']['fixed_for_date']
    for status,payload in [(429,b''),(302,b''),(200,b'x'*(LIMIT+1))]:
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(status,content=payload))) as client:
            with pytest.raises(Problem): fetch_gold_reference('008987',lambda:NOW,history(),client=client)
    def broken(req): raise httpx.ReadTimeout('synthetic')
    with httpx.Client(transport=httpx.MockTransport(broken)) as client:
        with pytest.raises(Problem): fetch_gold_reference('008987',lambda:NOW,history(),client=client)


def test_identity_official_gold_c_only():
    p = nav_body(code='008987',name='广发上海金ETF联接C')
    assert parse_public_nav(p,'008987','2026-10-21')['fund_code']=='008987'
    with pytest.raises(Problem): parse_public_nav(nav_body(code='008987',name='广发上海金ETF联接A'),'008987','2026-10-21')
