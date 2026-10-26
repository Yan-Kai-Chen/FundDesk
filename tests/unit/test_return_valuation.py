from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.domain.buckets import BucketBook
from app.domain.return_valuation import fund_return_valuation, portfolio_return_valuation


NOW = '2026-10-23T05:30:00Z'
DAY = '2026-10-23'


def holding(value='1.02', phase='LIVE'):
    book = BucketBook()
    book.buy('base', '600', '650', 'BASE', '2026-09-01T02:00:00Z', '2026-09-02')
    book.buy('t', '400', '420', 'TACTICAL', '2026-09-01T02:00:00Z', '2026-09-02')
    book.realized = {'BASE': D('-5'), 'TACTICAL': D('25')}
    fund = {'units': D('1000'), 'market_value': D('1000'), 'position_buckets': book.summary('1', DAY),
            'nav': {'id': 'formal', 'value': '1', 'effective_date': '2026-10-22', 'observed_at': '2026-10-22T12:00:00Z'},
            'daily_t': {'market': {'date': DAY}, 'today': {'status': 'READY', 'phase': phase, 'quote': {
                'id': 'live', 'value': value, 'effective_date': DAY, 'value_kind': 'THIRD_PARTY_ESTIMATE',
                'as_of': '2026-10-23T05:29:00Z', 'observed_at': NOW}}}}
    return fund, book


def test_live_revaluation_changes_floating_and_total_but_never_cost_realized_or_book():
    fund, book = holding()
    book.reserve('sell', '100', 'TACTICAL', DAY)
    before_fund, before_book = deepcopy(fund), deepcopy(book.__dict__)
    result = fund_return_valuation(fund, book, NOW)
    assert result['status'] == 'ESTIMATE' and result['label'] == '实时估算'
    assert result['holding_value'] == D('1020') and result['mark_change'] == D('20')
    assert result['summary']['total_pnl'] == D('-30') and result['official_total_pnl'] == D('-50')
    base, tactical = result['summary']['groups'].values()
    assert base['unrealized'] == D('-38') and tactical['unrealized'] == D('-12')
    assert base['realized'] == D('-5') and tactical['realized'] == D('25')
    assert base['pnl'] + tactical['pnl'] == result['summary']['total_pnl']
    assert tactical['reserved'] == D('100') and tactical['units'] == D('400')
    assert fund == before_fund and book.__dict__ == before_book


@pytest.mark.parametrize('phase,label', [('BREAK', '午间估算'), ('CLOSE', '收盘估算')])
def test_retained_lunch_and_closing_quotes_use_same_return_formula(phase, label):
    fund, book = holding(phase=phase)
    result = fund_return_valuation(fund, book, NOW)
    assert result['label'] == label and result['summary']['total_pnl'] == D('-30')


def test_formal_promotion_and_same_day_revision_do_not_double_add_price_change():
    fund, book = holding()
    live = fund_return_valuation(fund, book, NOW)
    quote = dict(fund['daily_t']['today']['quote'], value_kind='OFFICIAL_NAV')
    fund['nav'] = quote
    fund['market_value'] = D('1020')
    fund['position_buckets'] = book.summary('1.02', DAY)
    fund['daily_t']['today']['quote'] = quote
    official = fund_return_valuation(fund, book, NOW)
    assert official['summary'] == live['summary']
    assert official['mark_change'] == 0 and not official['is_estimate']
    assert official['label'] == '今日正式'
    quote['value'] = '1.03'
    fund['position_buckets'] = book.summary('1.03', DAY)
    fund['market_value'] = D('1030')
    revised = fund_return_valuation(fund, book, NOW)
    assert revised['summary']['total_pnl'] == D('-20') and revised['mark_change'] == 0


@pytest.mark.parametrize('status', ['STALE', 'NOT_OPEN', 'MISSING', 'CORPORATE_ACTION_REVIEW'])
def test_unusable_today_quote_keeps_explicit_last_formal_returns(status):
    fund, book = holding()
    fund['daily_t']['today']['status'] = status
    result = fund_return_valuation(fund, book, NOW)
    assert result['label'] == '最近正式' and result['date'] == '2026-10-22'
    assert result['summary']['total_pnl'] == D('-50') and result['mark_change'] == 0
    assert not result['current_for_date'] and not result['is_estimate']


@pytest.mark.parametrize('invalid', ['future', 'wrong_day', 'market_day', 'reference_only'])
def test_invalid_quote_cannot_revalue_returns(invalid):
    fund, book = holding()
    quote = fund['daily_t']['today']['quote']
    if invalid == 'future': quote['as_of'] = '2026-10-24T01:00:00Z'
    elif invalid == 'wrong_day': quote['effective_date'] = '2026-10-22'
    elif invalid == 'market_day': fund['daily_t']['market']['date'] = '2026-10-24'
    else: quote['value_kind'] = 'BENCHMARK_PRICE'
    assert fund_return_valuation(fund, book, NOW)['mark_change'] == 0


def test_cent_rounding_conserves_bucket_total_and_unknown_cost_stays_unknown():
    fund, _ = holding(value='1.01')
    book = BucketBook()
    book.buy('base', '.5', '.50', 'BASE', NOW)
    book.buy('t', '.5', '.50', 'TACTICAL', NOW)
    fund.update(units=D('1'), market_value=D('1'), position_buckets=book.summary('1', DAY))
    result = fund_return_valuation(fund, book, NOW)
    groups = result['summary']['groups']
    assert groups['BASE']['value'] + groups['TACTICAL']['value'] == D('1.01')
    assert groups['BASE']['pnl'] + groups['TACTICAL']['pnl'] == D('.01')
    book.lots['t|TACTICAL']['cost'] = None
    fund['position_buckets'] = book.summary('1', DAY)
    result = fund_return_valuation(fund, book, NOW)
    assert result['summary']['total_pnl'] is None
    assert result['summary']['groups']['TACTICAL']['unrealized'] is None
    assert result['holding_value'] == D('1.01')


def test_portfolio_revaluation_keeps_pending_cash_basis_and_labels_mixed_coverage():
    live, book = holding()
    stale = deepcopy(live)
    stale['daily_t']['today']['status'] = 'STALE'
    for f in (live, stale): f['return_valuation'] = fund_return_valuation(f, book, NOW)
    official = {'pnl': D('-100'), 'equity': D('2350'), 'market_value': D('2000'), 'pending_buy': D('350')}
    result = portfolio_return_valuation([live, stale], official)
    assert result['pnl'] == D('-80') and result['equity'] == D('2370')
    assert result['market_value'] == D('2020') and result['mark_change'] == D('20')
    assert result['status'] == 'MIXED' and result['current_count'] == 1 and result['last_official_count'] == 1
    assert sum(result['bucket_values'].values()) == result['market_value']
    assert official['pending_buy'] == D('350') and official['pnl'] == D('-100')


def test_unreconciled_holdings_never_create_a_full_portfolio_return():
    fund, book = holding()
    fund['initialization_pending'] = True
    fund['return_valuation'] = fund_return_valuation(fund, book, NOW)
    assert fund['return_valuation']['summary'] is None
    assert fund['return_valuation']['mark_change'] is None
    result = portfolio_return_valuation([fund], {'initialization_pending': True, 'pnl': None})
    assert result['pnl'] is None and result['status'] == 'WAIT'
