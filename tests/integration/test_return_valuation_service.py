from decimal import Decimal as D

from app.persistence.store import Store
from conftest import opening


def test_snapshot_revalues_existing_units_without_posting_or_adding_pending_buy(wb):
    with wb.store.transaction() as c:
        f = Store.doc(c, 'fund', wb.fid)
        Store.put(c, 'fund', wb.fid, dict(f, fund_code='012183', share_class='C'), f['_version'])
    opening(wb)
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-19', 'value': '1'})
    wb.observe({'fund_id': wb.fid, 'value_kind': 'THIRD_PARTY_ESTIMATE', 'effective_date': '2026-10-20',
                'value': '1.02', 'as_of': '2026-10-20T05:29:00Z'})
    wb.order({'fund_id': wb.fid, 'side': 'BUY', 'bucket': 'TACTICAL', 'requested': '200',
              'paid_immediately': True, 'occurred_at': wb.now(), 'key': 'pending-return-buy'})
    def rows():
        with wb.store.connect() as c:
            return {table: [tuple(r) for r in c.execute(f'SELECT * FROM {table}')]
                    for table in ['events', 'documents', 'observations', 'jobs', 'plans']}
    before = rows()
    live = wb.snapshot()
    f = live['funds'][0]
    assert f['return_valuation']['summary']['total_pnl'] == D('20')
    assert f['return_valuation']['summary']['total_units'] == D('1000')
    assert live['portfolio']['pending_buy'] == D('200')
    assert live['portfolio']['return_valuation']['pnl'] == D('20')
    composition=f['holding_composition']
    assert composition['total']['value']==D('1020') and composition['total']['pnl']==D('20')
    assert composition['groups']['BASE']['weight']==1 and composition['groups']['TACTICAL']['weight']==0
    assert live['portfolio']['pnl'] == 0 and f['position_buckets']['total_pnl'] == 0
    assert rows() == before and live == wb.snapshot()

    # Formal disclosure already contains the move. Promoting it must not add
    # the same daily effect to either the book or displayed cumulative P&L.
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1.02'})
    formal = wb.snapshot()
    assert formal['portfolio']['pnl'] == D('20')
    assert formal['portfolio']['return_valuation']['pnl'] == D('20')
    assert formal['funds'][0]['return_valuation']['is_estimate'] is False
    assert formal['funds'][0]['return_valuation']['mark_change'] == 0
