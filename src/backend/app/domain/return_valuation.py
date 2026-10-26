"""Read-only display valuation; the confirmed ledger stays on formal NAVs."""
from zoneinfo import ZoneInfo

from app.domain.finance import ZERO, dec, stamp


def _compact(summary):
    if not summary or not summary.get('groups'):
        return None
    return {key: summary.get(key) for key in ('total_pnl', 'holding_value', 'total_units')} | {
        'groups': {key: dict(value) for key, value in summary['groups'].items()}}


def fund_return_valuation(fund, book, now):
    """Revalue the same remaining units, cost and realized P&L at one quote.

    Reuse BucketBook's rounding and attribution. Never add a daily percentage
    to cumulative returns: a newly disclosed formal NAV already contains it.
    Eligibility, lunch/close retention and official priority come from daily_t.
    """
    today = fund['daily_t']['market']['date']
    current = fund['daily_t']['today']
    official = fund.get('nav') or {}
    original = fund.get('position_buckets') or {}
    summary = _compact(original)
    value = fund.get('market_value')
    formal_today = official.get('effective_date') == today
    result = {'status': 'OFFICIAL' if formal_today else 'LAST_OFFICIAL',
              'label': '今日正式' if formal_today else '最近正式', 'date': official.get('effective_date'),
              'as_of': official.get('observed_at'), 'quote_id': official.get('id'),
              'value_kind': 'OFFICIAL_NAV' if official else None, 'is_estimate': False,
              'current_for_date': formal_today,
              'summary': summary, 'holding_value': value,
              'official_total_pnl': original.get('total_pnl'), 'mark_change': ZERO if value is not None else None,
              'quote_status': current.get('status'), 'detail': current.get('label') or '等待当天报价'}
    if fund.get('initialization_pending') or original.get('status') == 'BLOCKED':
        return dict(result, status='HOLDINGS_REVIEW', label='持仓待核对', summary=None, current_for_date=False,
                    mark_change=None)
    quote = current.get('quote')
    valid = (today == stamp(now).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() and
             current.get('status') == 'READY' and quote and quote.get('effective_date') == today and
             quote.get('value_kind') in {'OFFICIAL_NAV', 'THIRD_PARTY_ESTIMATE', 'LOCAL_ESTIMATE'} and
             all(not quote.get(key) or stamp(quote[key]) <= stamp(now) for key in ('observed_at', 'as_of', 'published_at')))
    if not valid:
        return result
    if original.get('total_units') is None or dec(original['total_units']) != dec(fund.get('units', ZERO)):
        return dict(result, status='HOLDINGS_REVIEW', label='持仓待核对', summary=None, current_for_date=False,
                    mark_change=None)
    priced = book.summary(dec(quote['value'], positive=True), today)
    if not priced.get('groups'):
        return dict(result, status='HOLDINGS_REVIEW', label='持仓待核对', summary=None, current_for_date=False,
                    mark_change=None)
    estimate = quote['value_kind'] != 'OFFICIAL_NAV'
    phase = current.get('phase')
    label = ('今日正式' if not estimate else '午间估算' if phase == 'BREAK' else
             '收盘估算' if phase == 'CLOSE' else '实时估算')
    return dict(result, status='ESTIMATE' if estimate else 'OFFICIAL', label=label,
                date=quote['effective_date'], as_of=quote.get('as_of') or quote.get('observed_at'),
                quote_id=quote['id'], value_kind=quote['value_kind'], is_estimate=estimate,
                current_for_date=True, summary=_compact(priced), holding_value=priced['holding_value'],
                mark_change=priced['holding_value'] - dec(value) if value is not None else None,
                detail='已实现收益保持；持有盈亏按本次净值重估，估算不写入正式账本')


def portfolio_return_valuation(funds, official):
    held = [f for f in funds if dec(f.get('units', ZERO)) > ZERO or f.get('initialization_pending')]
    components = [f['return_valuation'] for f in held]
    current = sum(v['current_for_date'] for v in components)
    estimates = sum(v['is_estimate'] for v in components)
    complete = all(v['mark_change'] is not None for v in components) and not official.get('initialization_pending')
    mark = sum((v['mark_change'] for v in components), ZERO) if complete else None
    def revalue(key):
        return dec(official[key], nonnegative=False) + mark if mark is not None and official.get(key) is not None else None
    values = {}
    for key in ('BASE', 'TACTICAL'):
        rows = [(f['return_valuation'].get('summary') or {}).get('groups', {}).get(key) for f in funds]
        values[key] = (sum((dec(row['value']) for row in rows), ZERO)
                       if all(row and row.get('value') is not None for row in rows) else None)
    label = ('估值待补' if not complete else '含当日估算' if estimates else
             '今日正式' if held and current == len(held) else '最近正式')
    return {'status': 'READY' if complete and current == len(held) else 'MIXED' if complete else 'WAIT',
            'label': label, 'is_estimate': bool(estimates), 'holding_count': len(held),
            'current_count': current, 'estimate_count': estimates, 'last_official_count': len(held)-current,
            'mark_change': mark, 'pnl': revalue('pnl'), 'market_value': revalue('market_value'),
            'equity': revalue('equity'), 'bucket_values': values,
            'official_pnl': official.get('pnl')}
