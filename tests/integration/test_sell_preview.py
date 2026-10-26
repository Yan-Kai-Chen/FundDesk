from copy import deepcopy
from decimal import Decimal as D
import pytest
from fastapi.testclient import TestClient

from app.domain.finance import Problem
from app.main import create_app
from conftest import opening, event, order, confirm, AT


def seed(w, today_official=True):
    opening(w, '100', '120')
    event(w, 'OPENING_LOT', {'fund_id': w.fid, 'lot_id': 't-lot', 'units': '50', 'cost': '40',
                            'acquired': '2026-10-19', 'available_from': '2026-10-20', 'bucket': 'TACTICAL'})
    w.observe({'fund_id': w.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-19', 'value': '1'})
    if today_official:
        w.observe({'fund_id': w.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1'})


def test_sell_preview_fee_uses_physical_fifo_profit_uses_t_cost_and_matches_confirmation(wb):
    seed(wb)
    before = wb.snapshot()
    r = wb.preview_sell(wb.fid, {'requested': '70', 'bucket': 'TACTICAL', 'occurred_at': AT})
    assert r['status'] == 'READY' and r['fee'] == 0  # physical FIFO oldest base is fee-free
    assert r['removed_cost'] == D('64') and r['expected_realized_pnl'] == D('6')
    assert r['after']['groups']['TACTICAL']['realized'] == D('6')
    assert r['basis']['value_kind'] == 'OFFICIAL_NAV' and r['basis']['effective_date'] == '2026-10-20'
    after = wb.snapshot()
    assert before['revision'] == after['revision'] and before['lots'] == after['lots']
    assert before['funds'][0]['position_buckets'] == after['funds'][0]['position_buckets']
    assert before['orders'] == after['orders']
    oid = order(wb, 'SELL', '70', bucket='TACTICAL')
    confirm(wb, oid, '70', '1', '70', '0', '70')
    assert r['after']['groups'] == wb.snapshot()['funds'][0]['position_buckets']['groups']


def test_manual_physical_allocation_changes_fee_but_keeps_economic_t_attribution(wb):
    seed(wb)
    r = wb.preview_sell(wb.fid, {'requested': '10', 'allocations': [{'lot_id': 't-lot', 'units': '10'}]})
    assert r['fee'] == D('.15') and r['expected_realized_pnl'] == D('1.85')
    assert r['sell_effect'] == D('-.15') and r['fallback_units'] == 0


def test_current_quote_then_stale_fallback_are_labelled_and_do_not_change_formal_pnl(wb):
    seed(wb, today_official=False)
    with wb.store.transaction() as c:
        from app.persistence.store import Store
        fund = Store.doc(c, 'fund', wb.fid)
        Store.put(c, 'fund', wb.fid, dict(fund, fund_code='012183', share_class='C'), fund['_version'])
    wb.observe({'fund_id': wb.fid, 'value_kind': 'THIRD_PARTY_ESTIMATE', 'effective_date': '2026-10-20',
                'value': '1.1', 'as_of': '2026-10-20T05:29:00Z', 'source': 'Synthetic quote'})
    r = wb.preview_sell(wb.fid, {'requested': '10'})
    assert r['nav'] == D('1.1') and r['official_total_pnl'] == D('-10')
    assert r['basis']['value_kind'] == 'THIRD_PARTY_ESTIMATE'
    assert r['reference_mark_change'] == D('15') and r['sell_effect'] == 0
    assert r['before']['total_pnl'] == r['after']['total_pnl'] == D('5')
    assert wb.snapshot()['funds'][0]['position_buckets']['total_pnl'] == D('-10')
    # Today's disclosed official quote takes precedence over an estimate.
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1'})
    assert wb.preview_sell(wb.fid, {'requested': '10'})['nav'] == D('1')
    # Once tomorrow's session has started there is no usable current estimate;
    # last official NAV is an explicit reference, not a fabricated live quote.
    wb.now = lambda: '2026-10-21T05:30:00Z'
    r = wb.preview_sell(wb.fid, {'requested': '10'})
    assert r['basis']['effective_date'] == '2026-10-20' and r['nav'] == D('1')
    assert wb.snapshot()['funds'][0]['position_buckets']['total_pnl'] == D('-10')


@pytest.mark.parametrize('payload,code', [
    ({'requested': '0'}, 'INVALID_AMOUNT'), ({'requested': '0.001'}, 'UNIT_PRECISION'),
    ({'requested': 1.2}, 'INVALID_DECIMAL'), ({'requested': '151'}, 'UNITS_RESERVED'),
    ({'requested': '10', 'occurred_at': '2030-01-01T00:00:00Z'}, 'FUTURE_EVENT'),
    ({'requested': '10', 'bucket': 'OTHER'}, 'INVALID_BUCKET'),
    ({'requested': '10', 'allocations': {}}, 'INVALID_ALLOCATIONS'),
    ({'requested': '10', 'allocations': [{'lot_id': [], 'units': '10'}]}, 'INVALID_ALLOCATIONS'),
    ({'requested': '10', 'allocations': [{'lot_id': 'lot-a', 'units': '9'}]}, 'ALLOCATION_MISMATCH'),
    ({'requested': '10', 'allocations': [{'lot_id': 'lot-a', 'units': '5'}, {'lot_id': 'lot-a', 'units': '5'}]}, 'INVALID_ALLOCATIONS'),
])
def test_invalid_requests_never_write_or_reserve(wb, payload, code):
    seed(wb)
    before = deepcopy(wb.snapshot())
    with pytest.raises(Problem) as exc:
        wb.preview_sell(wb.fid, payload)
    assert exc.value.code == code
    after = wb.snapshot()
    assert before['revision'] == after['revision'] and before['lots'] == after['lots']


def test_api_session_csrf_mode_isolation_and_read_only(wb):
    seed(wb)
    app = create_app(wb.root, testing=True)
    app.state.workbenches['real'].now = wb.now
    with TestClient(app) as client:
        path = f'/api/v1/funds/{wb.fid}/sell-preview'
        assert client.post(path+'?mode=real', json={'requested': '10'}).status_code == 401
        csrf = client.get('/api/v1/session').json()['csrf_token']
        assert client.post(path+'?mode=real', json={'requested': '10'}).status_code == 403
        client.headers['x-csrf-token'] = csrf
        initial = client.get('/api/v1/state?mode=real').json()
        r = client.post(path+'?mode=real', json={'requested': '10'})
        assert r.status_code == 200 and r.json()['expected_realized_pnl'] == '2.00'
        assert r.json()['ledger_posted'] is False
        assert client.post(path+'?mode=demo', json={'requested': '10'}).json()['code'] == 'FUND_NOT_FOUND'
        assert client.get('/api/v1/state?mode=real').json() == initial
