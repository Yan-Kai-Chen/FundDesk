"""Morning references stay visible at lunch, while live/T freshness stays strict."""
from copy import deepcopy
from decimal import Decimal as D
import pytest

from app.domain.daily_t import today_quote, reference
from app.domain.finance import Problem
from app.domain.market_calendar import market_session
from app.providers.public_market import parse_ths_estimate, parse_fundgz
from test_daily_t import history, record, fund_and_context
from test_public_market import ths_body, fundgz_body


def morning_row():
    return dict(record('morning', '2026-10-20', '1.03', 'THIRD_PARTY_ESTIMATE', '2026-10-20T11:29:00+08:00'),
                quality='PUBLIC_THIRD_PARTY_ESTIMATE', observed_at='2026-10-20T11:29:00+08:00',
                provenance={'provider_id': 'THS_PUBLIC_ESTIMATE', 'reference_date': '2026-10-19', 'reference_nav': '1'})


@pytest.mark.parametrize('at', ['11:30:00', '11:50:00', '12:59:59'])
def test_sources_allow_morning_reference_through_lunch_only(at):
    now = '2026-10-20T' + at + '+08:00'
    assert parse_ths_estimate(ths_body(points='1129,1.23,1.2,0'), '012183', now)['morning_reference']
    assert parse_fundgz(fundgz_body(gztime='2026-10-20 11:29'), '012183', now)['morning_reference']
    # Even a missed end-of-morning collection is displayed with its true timestamp.
    assert parse_ths_estimate(ths_body(points='1000,1.23,1.2,0'), '012183', now)['morning_reference']
    for invalid in ['2026-10-20T13:00:00+08:00', '2026-10-21T12:30:00+08:00']:
        with pytest.raises(Problem):
            parse_ths_estimate(ths_body(points='1129,1.23,1.2,0'), '012183', invalid)


@pytest.mark.parametrize('scope', ['CN', 'CN_HK'])
def test_lunch_retains_same_return_and_resumes_strict_freshness(scope):
    row = morning_row()
    for at in ['11:30:00', '11:50:00', '12:59:59']:
        now = '2026-10-20T' + at + '+08:00'
        q = today_quote(history(), [row], now, market_session(now, scope))
        assert q['status'] == 'READY' and q['phase'] == 'BREAK'
        assert q['change'] == D('.03') and q['quote']['as_of'] == row['as_of']
        assert q['display_title'] == '午间涨跌' and '午间休市' in q['label']
    now = '2026-10-20T13:00:00+08:00'
    assert today_quote(history(), [row], now, market_session(now, scope))['status'] == 'STALE'
    fresh = dict(row, id='afternoon', value='1.04', as_of=now, observed_at=now)
    q = today_quote(history(), [row, fresh], now, market_session(now, scope))
    assert q['phase'] == 'LIVE' and q['change'] == D('.04')


@pytest.mark.parametrize('patch', [
    {'quality': 'MANUAL'}, {'value_kind': 'LOCAL_ESTIMATE'}, {'provenance': {}},
    {'effective_date': '2026-10-19'}, {'as_of': '2026-10-19T11:29:00+08:00'},
    {'observed_at': '2026-10-20T13:00:00+08:00'},
])
def test_lunch_does_not_extend_unverified_prior_day_or_future_evidence(patch):
    now = '2026-10-20T12:30:00+08:00'
    q = today_quote(history(), [dict(morning_row(), **patch)], now, market_session(now, 'CN_HK'))
    assert q['status'] != 'READY' and q['change'] is None


def test_current_baseline_still_required_and_lunch_never_enables_t():
    now = '2026-10-20T11:50:00+08:00'
    session = market_session(now, 'CN_HK')
    assert session['status'] == 'OPEN'  # HK may trade, but this source pauses at 11:30.
    revised = deepcopy(history()); revised[-1]['value'] = '1.01'
    assert today_quote(revised, [morning_row()], now, session)['status'] == 'QUOTE_BASIS_MISMATCH'
    f = fund_and_context(level='ACTIVE', now=now)
    f['daily_t']['today'] = today_quote(history(), [morning_row()], now, session)
    r = reference(f, [], now)
    assert r['status'] == 'BREAK' and r['amount'] is None and r['action'] == 'WAIT'
    # Gold's verified continuous day session is not paused by an equity-source rule.
    assert today_quote(history(), [morning_row()], now, market_session(now, 'GOLD_CN'))['status'] == 'STALE'
