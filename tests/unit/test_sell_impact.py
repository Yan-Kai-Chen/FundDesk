from copy import deepcopy
from decimal import Decimal as D
import pytest

from app.domain.buckets import BucketBook
from app.domain.finance import Problem, redemption_preview
from app.domain.sell_impact import sell_impact

AT = '2026-10-20T05:30:00Z'
ON = '2026-10-20'


def book():
    b = BucketBook()
    b.buy('base', '100', '120', 'BASE', '2026-06-01', '2026-06-03')
    b.buy('t', '50', '40', 'TACTICAL', '2026-10-19', '2026-10-20')
    b.realized['TACTICAL'] = D('3.25')
    return b


def redemption(units='70', gross='70', fee='1'):
    return dict(units=D(units), gross=D(gross), fee=D(fee), net=D(gross)-D(fee))


def test_overflow_matches_actual_economic_confirmation_and_does_not_mutate():
    b = book()
    original = deepcopy(b.summary('1', ON))
    result = sell_impact(original, redemption(), '1', ON, AT, official_total=original['total_pnl'])
    assert original == b.summary('1', ON)
    assert result['expected_realized_pnl'] == D('5')
    assert result['fallback_units'] == D('20') and result['fallback_principal'] == D('24')
    assert result['realized_before'] == D('3.25') and result['realized_after'] == D('8.25')
    assert result['before']['total_pnl'] == D('-6.75')
    assert result['after']['total_pnl'] == D('-7.75') and result['sell_effect'] == D('-1')
    assert result['before']['groups']['TACTICAL']['unrealized'] == D('10')
    assert result['after']['groups']['TACTICAL']['unrealized'] == 0
    b.reserve('actual', '70', 'TACTICAL', ON)
    b.sell('actual', '70', '69', '1', AT)
    b.release('actual')
    assert result['after']['groups'] == b.summary('1', ON)['groups']
    assert result['ledger_posted'] is False


def test_price_change_and_sale_effect_are_separate_no_double_count():
    b = book()
    official = b.summary('1', ON)
    result = sell_impact(official, redemption('10', '11', '0'), '1.1', ON, AT,
                         official_total=official['total_pnl'])
    assert result['reference_mark_change'] == D('15')
    assert result['sell_effect'] == 0 and result['rounding_effect'] == 0
    assert result['expected_realized_pnl'] == D('3')
    assert result['change_from_official'] == D('15')  # not 15+3


def test_sell_can_realize_loss_and_base_sell_never_consumes_t():
    result = sell_impact(book().summary('1', ON), redemption('10', '10', '0'), '1', ON, AT, 'BASE')
    assert result['expected_realized_pnl'] == D('-2')
    assert result['after']['groups']['TACTICAL'] == result['before']['groups']['TACTICAL']
    assert result['sell_effect'] == 0
    with pytest.raises(Problem, match='底仓'):
        sell_impact(book().summary('1'), redemption('101', '101', '0'), '1', ON, AT, 'BASE')


def test_reservations_availability_and_unknown_cost_are_not_fabricated():
    b = book()
    b.reserve('pending', '40', 'TACTICAL', ON)
    original = deepcopy(b.summary('1', ON))
    with pytest.raises(Problem):
        sell_impact(original, redemption('111'), '1', ON, AT)
    assert original == b.summary('1', ON)
    b.lots['t|TACTICAL']['cost'] = None
    r = sell_impact(b.summary('1', ON), redemption('10', '10', '0'), '1', ON, AT)
    assert r['status'] == 'UNKNOWN_COST' and r['expected_realized_pnl'] is None
    assert r['after']['total_pnl'] is None and r['sell_effect'] is None
    b.lots['t|TACTICAL']['available_from'] = None
    with pytest.raises(Problem) as exc:
        sell_impact(b.summary('1', ON), redemption('10'), '1', ON, AT)
    assert exc.value.code == 'AVAILABILITY_UNKNOWN'


def test_individual_channel_rounding_is_visible_in_total_projection():
    b = BucketBook()
    b.buy('t', '.03', '.03', 'TACTICAL', '2026-01-01', '2026-01-02')
    lots = [{'id': str(i), 'units': '.01', 'cost': '.01', 'acquired': '2026-01-01',
             'available_from': '2026-01-02'} for i in range(3)]
    rule = {'verified': True, 'lot_method': 'FIFO', 'redemption_tiers': [{'min_days': 0, 'rate': '0'}]}
    preview = redemption_preview(lots, '.02', '1.5', rule, ON)
    r = sell_impact(b.summary('1.5', ON), preview, '1.5', ON, AT)
    assert r['gross'] == D('.04') and r['expected_realized_pnl'] == D('.02')
    assert r['before']['holding_value'] == D('.05') and r['after']['holding_value'] == D('.02')
    assert r['rounding_effect'] == r['sell_effect'] == D('.01')


@pytest.mark.parametrize('summary', [None, {}, {'status': 'BLOCKED'}, {'groups': {'BASE': {}}}])
def test_unavailable_economic_book_blocks_preview(summary):
    with pytest.raises(Problem):
        sell_impact(summary, redemption(), '1', ON, AT)
