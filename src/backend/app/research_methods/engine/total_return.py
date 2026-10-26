"""A pure preprocessing formula, not a data source or corporate-action detector."""
from decimal import localcontext
from .domain import ZERO, ONE, positive, dec, require


def reconstruct(nav, cash_per_old_unit, new_units_per_old_unit):
    """Reinvested gross total-return index, initial level 100.

    Inputs are decimal strings on the same consecutive verified sessions.
    Cash dividends are per pre-event unit; split factors are new units / old unit.
    The caller must establish event completeness and point-in-time availability.
    """
    require(bool(nav) and len(nav) == len(cash_per_old_unit) == len(new_units_per_old_unit),
            "Aligned nonempty NAV/action arrays required")
    prices = [positive(x) for x in nav]
    dividends = [dec(x, ZERO) for x in cash_per_old_unit]
    splits = [positive(x) for x in new_units_per_old_unit]
    require(dividends[0] == ZERO and splits[0] == ONE,
            "Initial event requires a prior NAV outside this array")
    levels = [dec("100")]
    with localcontext() as context:
        context.prec = 36
        for i in range(1, len(prices)):
            levels.append(levels[-1] * (splits[i] * prices[i] + dividends[i]) / prices[i - 1])
    return levels
