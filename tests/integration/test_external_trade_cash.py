from copy import deepcopy
from decimal import Decimal as D
import pytest
from fastapi.testclient import TestClient

from app.domain.finance import Problem
from app.domain.ledger import project
from app.main import create_app
from app.persistence.store import Store
from conftest import AT, event, deposit, order, confirm


def real_order(w, side='BUY', requested='200', **extra):
    return w.order({'fund_id': w.fid, 'side': side, 'bucket': 'TACTICAL', 'requested': requested,
                    'occurred_at': AT, 'key': 'external-'+str(w.snapshot()['revision']), **extra})['order_id']


@pytest.mark.parametrize('bucket', ['BASE', 'TACTICAL'])
def test_real_buy_needs_no_cash_balance_and_confirmation_keeps_true_pnl(wb, bucket):
    oid = real_order(wb, bucket=bucket)
    s = wb.snapshot()
    assert s['orders'][0]['cash_source'] == 'EXTERNAL'
    assert s['portfolio']['cash'] == s['portfolio']['reserved_cash'] == s['portfolio']['available_cash'] == 0
    assert s['portfolio']['pending_buy'] == s['portfolio']['pnl'] == 0
    event(wb, 'ORDER_PAID', {'order_id': oid, 'amount': '200'})
    s = wb.snapshot()
    assert s['portfolio']['equity'] == s['portfolio']['pending_buy'] == D('200')
    assert s['portfolio']['cash'] == s['portfolio']['pnl'] == 0
    confirm(wb, oid, '200', '1', '200', '0', '200', available_from='2026-10-20')
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1.1'})
    s = wb.snapshot()
    assert s['portfolio']['equity'] == D('220') and s['portfolio']['pnl'] == D('20')
    assert s['funds'][0]['position_buckets']['groups'][bucket]['pnl'] == D('20')
    assert s['portfolio']['cash'] == s['portfolio']['reserved_cash'] == 0
    assert not any(e['kind'] == 'CASH_DEPOSIT' for e in s['audit'])


def test_external_sale_settlement_keeps_profit_without_accumulating_wallet_cash(wb):
    oid = real_order(wb, paid_immediately=True)
    confirm(wb, oid, '200', '1', '200', '0', '200', available_from='2026-10-20')
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1.1'})
    sale = real_order(wb, 'SELL', '50')
    confirm(wb, sale, '50', '1.1', '55', '.50', '54.50')
    s = wb.snapshot()
    assert s['portfolio']['redemption_due'] == D('54.50') and s['portfolio']['pnl'] == D('19.50')
    event(wb, 'ORDER_SETTLED', {'order_id': sale, 'amount': '54.50'})
    s = wb.snapshot()
    assert s['portfolio']['equity'] == D('165') and s['portfolio']['pnl'] == D('19.50')
    assert s['portfolio']['cash'] == s['portfolio']['redemption_due'] == 0
    assert s['funds'][0]['position_buckets']['total_pnl'] == D('19.50')
    before = s['revision']
    with pytest.raises(Problem): event(wb, 'ORDER_SETTLED', {'order_id': sale, 'amount': '.01'})
    assert wb.snapshot()['revision'] == before


def test_partial_confirmation_cancel_and_external_refund_conserve_cost_and_fee(wb):
    oid = real_order(wb, requested='500', paid_immediately=True)
    confirm(wb, oid, '99', '1', '100', '1', '99', False)
    event(wb, 'ORDER_CANCELED', {'order_id': oid})
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1'})
    assert wb.snapshot()['portfolio']['refund_due'] == D('400')
    event(wb, 'ORDER_REFUNDED', {'order_id': oid, 'amount': '400'})
    s = wb.snapshot()
    assert s['portfolio']['cash'] == s['portfolio']['refund_due'] == 0
    assert s['portfolio']['pnl'] == D('-1') and s['portfolio']['equity'] == D('99')
    assert s['funds'][0]['position_buckets']['total_pnl'] == D('-1')


def test_external_buy_is_independent_of_legacy_reserved_cash_and_payment_guard_remains(wb):
    deposit(wb, '100')
    tracked = order(wb, requested='100')
    external = real_order(wb, requested='200', paid_immediately=True)
    s = wb.snapshot()
    assert s['portfolio']['cash'] == s['portfolio']['reserved_cash'] == D('100')
    assert s['portfolio']['available_cash'] == 0 and s['portfolio']['pending_buy'] == D('200')
    event(wb, 'ORDER_PAID', {'order_id': tracked, 'amount': '100'})
    before = wb.snapshot()['revision']
    with pytest.raises(Problem): event(wb, 'ORDER_PAID', {'order_id': external, 'amount': '.01'})
    assert wb.snapshot()['revision'] == before and wb.snapshot()['portfolio']['cash'] == 0


def test_old_events_without_scope_replay_unchanged(wb):
    deposit(wb, '300')
    order(wb, requested='200', paid_immediately=True)
    with wb.store.connect() as c: rows = Store.events(c)
    old = deepcopy(rows)
    for e in old:
        if e['kind'] == 'ORDER_SUBMITTED': e['payload'].pop('cash_source')
    original, replayed = project(rows), project(old)
    for field in ['cash', 'external_net', 'reserved_cash', 'pending_buy', 'refund_due', 'redemption_due']:
        assert original[field] == replayed[field]
    assert replayed['cash'] == D('100')


def test_real_csv_buy_paid_requires_no_deposit_and_keeps_refund_external(wb):
    content = 'external_order_id,fund_code,side,submitted_at,requested_amount_cny,payment_state\nEXTERNAL-ROW,000001,BUY,2026-10-20T13:00:00+08:00,200,PAID\n'
    preview = wb.import_preview('orders', content)
    assert not preview['errors']
    wb.import_commit(preview['id'], preview['source_hash'])
    s = wb.snapshot(); oid = s['orders'][0]['id']
    assert s['portfolio']['cash'] == 0 and s['portfolio']['pending_buy'] == D('200')
    assert s['orders'][0]['cash_source'] == 'EXTERNAL'
    event(wb, 'ORDER_CANCELED', {'order_id': oid}, at='2026-10-20T05:30:00Z')
    event(wb, 'ORDER_REFUNDED', {'order_id': oid, 'amount': '200'}, at='2026-10-20T05:30:00Z')
    assert wb.snapshot()['portfolio']['cash'] == wb.snapshot()['portfolio']['equity'] == wb.snapshot()['portfolio']['pnl'] == 0


def test_real_api_defaults_external_and_retries_do_not_duplicate_payments(wb):
    app = create_app(wb.root, testing=True)
    app.state.workbenches['real'].now = wb.now
    with TestClient(app) as c:
        csrf = c.get('/api/v1/session').json()['csrf_token']; c.headers['x-csrf-token'] = csrf
        p = {'fund_id': wb.fid, 'side': 'BUY', 'bucket': 'BASE', 'requested': '200',
             'occurred_at': AT, 'key': 'zero-cash-real-buy', 'paid_immediately': True}
        first = c.post('/api/v1/orders?mode=real', json=p)
        assert first.status_code == 200
        assert c.post('/api/v1/orders?mode=real', json=p).json()['duplicate']
        s = c.get('/api/v1/state?mode=real').json()
        assert s['revision'] == 2 and s['portfolio']['cash'] == '0' and s['portfolio']['pending_buy'] == '200'
        assert s['orders'][0]['cash_source'] == 'EXTERNAL'
        for invalid in ['OTHER', None, []]:
            r = c.post('/api/v1/orders?mode=real', json=dict(p, key='invalid-cash-'+str(invalid), cash_source=invalid))
            assert r.status_code == 400 and r.json()['code'] == 'INVALID_CASH_SOURCE'
        assert c.get('/api/v1/state?mode=real').json()['revision'] == s['revision']
