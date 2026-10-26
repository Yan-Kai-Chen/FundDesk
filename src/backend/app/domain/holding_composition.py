"""Compact, read-only allocation and profit composition for one displayed fund."""
from decimal import Decimal, ROUND_HALF_UP

from app.domain.finance import ZERO, dec

ONE = Decimal('1')
PRECISION = Decimal('.000000000001')


def _ratio(numerator, denominator):
    return (numerator / denominator).quantize(PRECISION, rounding=ROUND_HALF_UP) if denominator else None


def holding_composition(summary):
    source = (summary or {}).get('groups') or {}
    groups = {}
    for key in ('BASE', 'TACTICAL'):
        row = source.get(key) or {}
        groups[key] = {k: dec(row[k], nonnegative=False) if row.get(k) is not None else None
                       for k in ('units', 'value', 'cost', 'unrealized', 'realized', 'pnl')}
    def total(field):
        values = [r[field] for r in groups.values()]
        return sum(values, ZERO) if all(v is not None for v in values) else None
    amounts = {k: total(k) for k in ('units', 'value', 'cost', 'unrealized', 'realized', 'pnl')}
    value, cost = amounts['value'], amounts['cost']
    amounts['holding_return'] = (_ratio(amounts['unrealized'], cost)
        if amounts['unrealized'] is not None and cost is not None and cost > ZERO else None)
    weights = value is not None and value > ZERO
    known_pnl = all(g['pnl'] is not None for g in groups.values())
    magnitude = sum((abs(g['pnl']) for g in groups.values()), ZERO) if known_pnl else None
    base_weight = _ratio(groups['BASE']['value'], value) if weights else None
    base_pnl_weight = _ratio(abs(groups['BASE']['pnl']), magnitude) if known_pnl and magnitude else None
    for key, row in groups.items():
        row['weight'] = (base_weight if key == 'BASE' else ONE-base_weight) if weights else None
        row['pnl_weight'] = ((base_pnl_weight if key == 'BASE' else ONE-base_pnl_weight)
                             if base_pnl_weight is not None else None)
        row['holding_return'] = (_ratio(row['unrealized'], row['cost']) if row['unrealized'] is not None and
                                 row['cost'] is not None and row['cost'] > ZERO else None)
        row['pnl_direction'] = ('UNKNOWN' if row['pnl'] is None else 'GAIN' if row['pnl'] > ZERO else
                                'LOSS' if row['pnl'] < ZERO else 'FLAT')
    return {'status': 'UNKNOWN' if value is None else 'EMPTY' if not value else 'READY',
            'total': amounts, 'groups': groups,
            'pnl_status': 'UNKNOWN' if magnitude is None else 'FLAT' if not magnitude else 'READY',
            'pnl_magnitude': magnitude, 'pnl_weight_basis': 'ABSOLUTE_CUMULATIVE_PNL',
            'holding_return_basis': 'UNREALIZED_OVER_REMAINING_COST'}
