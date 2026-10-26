from datetime import datetime, timedelta
from decimal import Decimal as D
import hashlib
import json

import httpx
import pytest

from app.domain.finance import Problem, SHANGHAI
from app.providers.public_nav import fetch_public_nav, parse_latest_nav


def history(end='2026-10-22', last='1'):
    finish = datetime.fromisoformat(end).replace(tzinfo=SHANGHAI)
    rows = [{'x': int((finish-timedelta(days=29-i)).timestamp()*1000),
             'y': last if i == 29 else '1', 'unitMoney': ''} for i in range(30)]
    return ('var fS_name="广发沪港深精选混合C";var fS_code="012183";var Data_netWorthTrend='+
            json.dumps(rows)+';').encode()


def latest(value='1.081', date_='2026-10-23', **change):
    finish = datetime.fromisoformat(date_)
    rows = [dict(FSRQ=(finish-timedelta(days=i)).date().isoformat(),
                 DWJZ=value if i == 0 else '1', NAVTYPE='1', FHSP='', FHFCBZ='', FHFCZ='', FHFCZ10='')
            for i in range(5)]
    rows[0].update(change)
    return json.dumps({'ErrCode':0, 'Data':{'LSJZList':rows}}).encode()


def fetch(primary=None, recent=None, api_status=200):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.host == 'fund.eastmoney.com':
            return httpx.Response(200, headers={'content-type':'application/javascript'}, content=primary or history())
        return httpx.Response(api_status, content=recent or latest())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=fetch_public_nav('012183','2026-10-23',client=client)
    return result, calls


def test_successful_but_yesterday_chart_is_supplemented_with_today_disclosure():
    result, calls=fetch()
    assert result['records'][-1]['effective_date']=='2026-10-23'
    assert result['records'][-1]['value']==D('1.081')
    assert len(result['records'])==31 and result['freshness_source']=='EASTMONEY_HISTORY_API'
    assert calls[0].url.params['v'] and calls[1].url.params['_']
    assert calls[1].url.params['fundCode']=='012183'
    assert all(r.headers['cache-control']=='no-cache' for r in calls)
    assert hashlib.sha256(result['raw_body']).hexdigest()==result['raw_sha256']
    evidence=json.loads(result['raw_body'])
    assert evidence['history']==history().decode() and evidence['latest']==latest().decode()


def test_same_day_revision_prefers_latest_api_without_creating_a_second_date():
    result,_=fetch(primary=history(end='2026-10-23',last='1.02'),recent=latest(value='1.03'))
    assert result['records'][-1]['value']==D('1.03') and len(result['records'])==30


def test_an_old_latest_response_never_rolls_back_newer_chart():
    result,_=fetch(primary=history(end='2026-10-23',last='1.03'),recent=latest(date_='2026-10-22'))
    assert result['records'][-1]['value']==D('1.03')


def test_recent_action_is_retained_instead_of_silently_marking_action_free():
    result,_=fetch(recent=latest(FHSP='每份分红0.10元'))
    assert result['records'][-1]['corporate_action']=='每份分红0.10元'


@pytest.mark.parametrize('payload', [latest(date_='2026-10-24'),latest(FCODE='012182'),
    latest(DWJZ='NaN'),latest(DWJZ='0'),latest(NAVTYPE='2'),b'<html>blocked</html>',b'x'*65537,
    b'{"ErrCode":0,"Data":{"LSJZList":[]}}'])
def test_invalid_latest_data_keeps_verified_history_and_exposes_failure(payload):
    result,_=fetch(recent=payload)
    assert result['records'][-1]['effective_date']=='2026-10-22'
    assert result['fallback_errors'][0]['provider']=='EASTMONEY_HISTORY_API'


def test_inconsistent_fund_history_anchor_cannot_be_used_as_new_nav():
    obj=json.loads(latest())
    obj['Data']['LSJZList'][2]['DWJZ']='9.99'
    result,_=fetch(recent=json.dumps(obj).encode())
    assert result['records'][-1]['effective_date']=='2026-10-22'
    assert result['fallback_errors'][0]['code']=='SOURCE_IDENTITY_MISMATCH'


@pytest.mark.parametrize('status',[429,503,302])
def test_supplementary_outage_keeps_a_valid_chart(status):
    result,_=fetch(api_status=status)
    assert result['records'][-1]['effective_date']=='2026-10-22'
    assert result['fallback_errors']


def test_duplicate_dates_and_script_payloads_are_rejected():
    data=json.loads(latest());data['Data']['LSJZList'][1]['FSRQ']='2026-10-23'
    for payload in [json.dumps(data).encode(),latest()+b';evil()']:
        with pytest.raises(Problem):parse_latest_nav(payload,'012183','2026-10-23')
