"""Closing references retain their date and evidence, not live quote validity."""
from copy import deepcopy
from decimal import Decimal as D
import pytest

from app.domain.daily_t import today_quote, reference
from app.domain.finance import Problem
from app.domain.market_calendar import market_session
from app.providers.public_market import parse_ths_estimate, parse_fundgz
from test_daily_t import history, record, fund_and_context
from test_public_market import ths_body, fundgz_body


def closing_row():
    return dict(record('close', '2026-10-20', '1.03', 'THIRD_PARTY_ESTIMATE', '2026-10-20T15:00:00+08:00'),
                quality='PUBLIC_THIRD_PARTY_ESTIMATE',
                observed_at='2026-10-20T15:20:00+08:00',
                provenance={'provider_id': 'THS_PUBLIC_ESTIMATE', 'quote_phase': 'SOURCE_CLOSE',
                            'reference_date': '2026-10-19', 'reference_nav': '1'})


@pytest.mark.parametrize('at', ['15:00:00', '15:45:00', '23:59:59'])
def test_source_final_point_accepted_for_same_day_only(at):
    now = '2026-10-20T'+at+'+08:00'
    assert parse_ths_estimate(ths_body(points='1459,1.22,1.2,0;1500,1.23,1.2,0'), '012183', now)['closing_reference']
    assert parse_fundgz(fundgz_body(gztime='2026-10-20 15:00'), '012183', now)['closing_reference']
    for body in [ths_body(points='1459,1.23,1.2,0'), ths_body(date_='2026-10-19', points='1500,1.23,1.2,0')]:
        if at == '15:00:00' and b'1459' in body:
            assert not parse_ths_estimate(body, '012183', now)['closing_reference']
        else:
            with pytest.raises(Problem): parse_ths_estimate(body, '012183', now)
    with pytest.raises(Problem):
        parse_ths_estimate(ths_body(points='1500,1.23,1.2,0'), '012183', '2026-10-21T10:00:00+08:00')
    with pytest.raises(Problem):
        parse_ths_estimate(ths_body(points='1500,1.23,1.2,0'), '012183', '2026-10-20T14:59:59+08:00')


@pytest.mark.parametrize('scope', ['CN', 'CN_HK'])
def test_after_close_estimate_remains_visible_then_official_replaces_it(scope):
    now = '2026-10-20T23:30:00+08:00'
    session = market_session(now, scope)
    q = today_quote(history(), [closing_row()], now, session)
    assert q['status'] == 'READY' and q['phase'] == 'CLOSE'
    assert q['display_title'] == '收盘涨跌' and q['label'] == '15:00 收盘估算' and q['change'] == D('.03')
    formal = record('official', '2026-10-20', '1.025', as_of='2026-10-20T21:00:00+08:00')
    q = today_quote(history()+[formal], [closing_row()], now, session)
    assert q['quote']['id'] == 'official' and q['phase'] == 'OFFICIAL' and q['change'] == D('.025')
    tomorrow = '2026-10-21T10:00:00+08:00'
    assert today_quote(history()+[formal], [closing_row()], tomorrow, market_session(tomorrow, scope))['status'] == 'MISSING'


@pytest.mark.parametrize('patch', [
    {'quality': 'MANUAL'}, {'value_kind': 'LOCAL_ESTIMATE'}, {'provenance': {}},
    {'as_of': '2026-10-20T14:59:00+08:00'},
    {'provenance': {'provider_id': 'UNVERIFIED', 'quote_phase': 'SOURCE_CLOSE', 'reference_date': '2026-10-19', 'reference_nav': '1'}},
])
def test_unverified_or_nonfinal_point_never_gets_extended_freshness(patch):
    now = '2026-10-20T22:00:00+08:00'
    q = today_quote(history(), [dict(closing_row(), **patch)], now, market_session(now, 'CN_HK'))
    assert q['status'] == 'STALE' and q['change'] is None and q['label'] == '收盘数据待更新'


def test_closing_reference_requires_current_basis_and_does_not_enable_late_t():
    now = '2026-10-20T15:25:00+08:00'
    revised = deepcopy(history()); revised[-1]['value'] = '1.01'
    assert today_quote(revised, [closing_row()], now, market_session(now, 'CN_HK'))['status'] == 'QUOTE_BASIS_MISMATCH'
    f = fund_and_context(level='ACTIVE', now=now)
    f['daily_t']['today'] = today_quote(history(), [closing_row()], now, f['daily_t']['market'])
    assert f['daily_t']['today']['status'] == 'READY'
    r = reference(f, [], now)
    assert r['status'] == 'CUTOFF' and r['amount'] is None and r['action'] == 'WAIT'


def test_newer_closing_revision_replaces_same_timestamp_estimate_and_formal_still_wins():
    now='2026-10-20T22:00:00+08:00';session=market_session(now,'CN')
    original=closing_row()
    revised=dict(original,id='updated',value='1.04',observed_at='2026-10-20T16:10:00+08:00')
    q=today_quote(history(),[revised,original],now,session)
    assert q['quote']['id']=='updated' and q['change']==D('.04')
    formal=record('formal','2026-10-20','1.035',as_of='2026-10-20T21:00:00+08:00')
    assert today_quote(history()+[formal],[revised],now,session)['change']==D('.035')
