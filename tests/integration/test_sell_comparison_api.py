from copy import deepcopy
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import opening, event, order


def seed(w):
    opening(w, units='1000', cost='1200')
    w.observe({'fund_id': w.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1'})
    oid = order(w, 'BUY', '90', bucket='BASE', cash_source='EXTERNAL', paid_immediately=True)
    event(w, 'ORDER_CONFIRMED', {'order_id': oid, 'units': '100', 'nav': '.9', 'gross': '90', 'fee': '0',
          'net': '90', 'nav_date': '2026-10-20', 'confirm_date': '2026-10-20', 'available_from': '2026-10-21', 'is_final': True})
    return oid


def test_price_comparison_can_be_positive_while_actual_sale_is_loss_and_gets_no_booking(wb):
    oid = seed(wb)
    before = deepcopy(wb.snapshot())
    result = wb.preview_sell(wb.fid, {'requested': '200', 'bucket': 'TACTICAL'})
    assert result['expected_realized_pnl'] == -40 and result['holding_pnl_before_fee'] == -40
    r = result['buy_comparison']
    assert r['status'] == 'READY' and r['order_id'] == oid and r['bucket'] == 'BASE'
    assert r['net_price_edge'] == 10 and r['covered_units'] == r['uncovered_units'] == 100
    assert r['available_units'] == 0 and result['fallback_units'] == 200
    assert not r['included_in_total_pnl'] and wb.snapshot() == before
    # Comparing a purchase never changes the cost deducted by an actual sale.
    other = wb.preview_sell(wb.fid, {'requested': '200', 'bucket': 'TACTICAL', 'reference_buy_order_id': oid})
    for key in ('expected_realized_pnl', 'removed_cost', 'fee', 'net', 'after', 'sell_effect'):
        assert result[key] == other[key]


def test_comparison_endpoint_retains_csrf_scope_and_read_only(wb):
    oid = seed(wb)
    app = create_app(wb.root, testing=True)
    app.state.workbenches['real'].now = wb.now
    with TestClient(app) as client:
        path = f'/api/v1/funds/{wb.fid}/sell-preview?mode=real'
        payload = {'requested': '200', 'bucket': 'TACTICAL', 'reference_buy_order_id': oid}
        assert client.post(path, json=payload).status_code == 401
        csrf = client.get('/api/v1/session').json()['csrf_token']
        assert client.post(path, json=payload).status_code == 403
        client.headers['x-csrf-token'] = csrf
        before = client.get('/api/v1/state?mode=real').json()
        result = client.post(path, json=payload)
        assert result.status_code == 200 and result.json()['buy_comparison']['net_price_edge'] == '10.00'
        payload['reference_buy_order_id'] = 'foreign'
        assert client.post(path, json=payload).json()['code'] == 'BUY_REFERENCE_NOT_FOUND'
        payload['reference_buy_order_id'] = []
        assert client.post(path, json=payload).json()['code'] == 'INVALID_BUY_REFERENCE'
        assert client.get('/api/v1/state?mode=real').json() == before
