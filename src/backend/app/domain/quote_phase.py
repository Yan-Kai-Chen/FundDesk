"""Recognize verified paused-session records without relaxing live freshness."""
from datetime import time

from app.domain.finance import SHANGHAI, stamp
from app.domain.market_calendar import market_session

EQUITY_CLOSE_PROVIDERS = {'THS_PUBLIC_ESTIMATE', 'EASTMONEY_PUBLIC_ESTIMATE'}


def source_lunch(now, scope):
    # Equity estimate sources pause at 11:30 even when HK is still trading.
    return scope in {'CN', 'CN_HK'} and market_session(now, 'CN')['status'] == 'BREAK'


def morning_reference(as_of, now, scope='CN'):
    if not as_of or not source_lunch(now, scope):
        return False
    at, local = stamp(as_of).astimezone(SHANGHAI), stamp(now).astimezone(SHANGHAI)
    return at.date() == local.date() and time(9, 30) <= at.time() <= time(11, 30)


def lunch_reference(row, now, scope):
    return (row.get('value_kind') == 'THIRD_PARTY_ESTIMATE' and
            row.get('quality') == 'PUBLIC_THIRD_PARTY_ESTIMATE' and
            (row.get('provenance') or {}).get('provider_id') in EQUITY_CLOSE_PROVIDERS and
            row.get('effective_date') == stamp(now).astimezone(SHANGHAI).date().isoformat() and
            morning_reference(row.get('as_of'), now, scope))


def closing_reference(row, today):
    p = row.get('provenance') or {}
    if row.get('effective_date') != today or not row.get('as_of'):
        return False
    at = stamp(row['as_of']).astimezone(SHANGHAI)
    if at.date().isoformat() != today:
        return False
    equity = (row.get('value_kind') == 'THIRD_PARTY_ESTIMATE' and
              row.get('quality') == 'PUBLIC_THIRD_PARTY_ESTIMATE' and
              p.get('provider_id') in EQUITY_CLOSE_PROVIDERS and
              p.get('quote_phase') == 'SOURCE_CLOSE' and at.time() == time(15))
    gold = (row.get('value_kind') == 'LOCAL_ESTIMATE' and row.get('quality') == 'LOCAL_GOLD_REFERENCE' and
            p.get('provider_id') == 'SGE_GOLD_REFERENCE' and p.get('model_version') == 'shau-gold-v1' and
            p.get('reference_mode') == 'DAY_CLOSE_PROXY' and at.time() == time(15, 30))
    return equity or gold


def after_source_close(now, scope):
    if scope not in {'CN', 'CN_HK', 'GOLD_CN'}:
        return False
    return stamp(now).astimezone(SHANGHAI).time() >= (time(15, 30) if scope == 'GOLD_CN' else time(15))
