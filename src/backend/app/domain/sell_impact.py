"""Read-only economic sell projection; channel fees arrive from physical lots."""
from copy import deepcopy

from app.domain.buckets import BUCKETS, BucketBook, bucket
from app.domain.finance import ZERO, dec, require


def difference(a, b):
    return None if a is None or b is None else a - b


def sell_impact(summary, redemption, nav, on, at, intent='TACTICAL', official_total=None):
    """Replay the same reserve/sell rules on a copy, at one fixed reference NAV.

    Realized profit is a reclassification of holding profit, not an addition to
    total profit. Group valuation retains the book's penny-rounding policy.
    """
    bucket(intent)
    require(summary and summary.get('groups') and summary.get('status') != 'BLOCKED',
            'BUCKET_PREVIEW_UNAVAILABLE', '分仓口径待核对，暂不能计算卖出收益')
    require(isinstance(summary.get('positions'), list), 'BUCKET_PREVIEW_UNAVAILABLE', '缺少可核对的分仓批次')
    b = BucketBook()
    for raw in summary['positions']:
        lot = deepcopy(raw)
        for key in ('units', 'reserved', 'cost'):
            lot[key] = None if key == 'cost' and lot.get(key) is None else dec(lot.get(key, ZERO))
        b.lots[lot['id']] = lot
    b.realized = {k: None if summary['groups'][k]['realized'] is None else
                  dec(summary['groups'][k]['realized'], nonnegative=False) for k in BUCKETS}
    before = b.summary(nav, on)
    allocations = b.reserve('sell-preview', redemption['units'], intent, on)
    require(all(b.lots[a['lot_id']].get('available_from') for a in allocations),
            'AVAILABILITY_UNKNOWN', '所用分仓批次可卖日期未知，暂不能预览收益')
    sale = b.sell('sell-preview', redemption['units'], redemption['net'], redemption['fee'], at)
    b.release('sell-preview')
    after = b.summary(nav, on)
    for result in (before, after):
        for key in ('positions', 'sales', 'legacy_count', 'policy', 'status'):
            result.pop(key, None)
    official = None if official_total is None else dec(official_total, nonnegative=False)
    return {'status': 'READY' if sale['pnl'] is not None and before['total_pnl'] is not None else 'UNKNOWN_COST',
            'bucket': intent, 'nav': dec(nav, positive=True), 'fee_reference_date': on,
            'units': redemption['units'], 'gross': redemption['gross'], 'fee': redemption['fee'], 'net': redemption['net'],
            'expected_realized_pnl': sale['pnl'], 'removed_cost': sale['cost'],
            'holding_pnl_before_fee': difference(redemption['gross'], sale['cost']),
            't_units': sale['t_units'], 'base_units': sale['base_units'],
            'fallback_units': sale['fallback_units'], 'fallback_principal': sale['fallback_principal'],
            'before': before, 'after': after,
            'realized_before': before['groups'][intent]['realized'], 'realized_after': after['groups'][intent]['realized'],
            'official_total_pnl': official,
            'reference_mark_change': difference(before['total_pnl'], official),
            'sell_effect': difference(after['total_pnl'], before['total_pnl']),
            'change_from_official': difference(after['total_pnl'], official),
            'rounding_effect': difference(redemption['gross'], before['holding_value'] - after['holding_value']),
            'policy': 'T_FIRST_THEN_BASE_ALL_PNL_TO_T', 'ledger_posted': False}
