"""Synthetic checks for explanations and bounded pre-disclosure estimate receipts."""
from datetime import date, timedelta
from decimal import Decimal as D
import hashlib

import pytest

from app.persistence.store import Store
from app.services.valuation_explanation import DEVIATION_KIND
from conftest import opening, order, confirm


def rows(w):
    with w.store.connect() as c:
        return {table: [tuple(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in ('events', 'documents', 'observations', 'jobs', 'plans')}


def supported(w):
    with w.store.transaction() as c:
        f = Store.doc(c, 'fund', w.fid)
        Store.put(c, 'fund', w.fid, dict(f, fund_code='012183', share_class='C'), f['_version'])


def official(w, day='2026-10-20', value='1.01', *, observed=None, quality='MANAGER_DISCLOSURE'):
    return w.observe({'fund_id': w.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': day, 'value': value,
                      'observed_at': observed or day+'T12:00:00Z', 'source': 'Synthetic formal disclosure', 'quality': quality})['id']


def close(w, day='2026-10-20', value='1.02', *, observed=None, reference_date='2026-10-19',
          reference_nav='1', as_of=None, phase='SOURCE_CLOSE', provider='THS_PUBLIC_ESTIMATE',
          quality='PUBLIC_THIRD_PARTY_ESTIMATE'):
    evidence = {'provider_id': provider, 'reference_date': reference_date, 'reference_nav': reference_nav,
                'source_url': 'https://example.invalid/synthetic', 'quote_phase': phase,
                'raw_response_hash': hashlib.sha256((day+value).encode()).hexdigest()}
    return w.observe({'fund_id': w.fid, 'value_kind': 'THIRD_PARTY_ESTIMATE', 'effective_date': day,
                      'value': value, 'as_of': as_of or day+'T07:00:00Z', 'observed_at': observed or day+'T07:05:00Z',
                      'source': 'Synthetic close source', 'quality': quality}, provider_evidence=evidence)['id']


def receipt(w, day='2026-10-20'):
    with w.store.connect() as c:
        return Store.doc(c, DEVIATION_KIND, w.fid+':'+day)


def test_current_marked_return_equation_estimate_to_formal_has_no_duplicate_pnl(wb):
    supported(wb); opening(wb)
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-19', 'value': '1'})
    wb.observe({'fund_id': wb.fid, 'value_kind': 'THIRD_PARTY_ESTIMATE', 'effective_date': '2026-10-20',
                'value': '1.02', 'as_of': '2026-10-20T05:29:00Z', 'source': 'Synthetic live'})
    before = rows(wb)
    live = wb.valuation_explanation(wb.fid)
    components = live['components']
    assert components['pnl'] == components['value'] - components['cost'] + components['realized'] == D('20')
    assert components['official_pnl'] + components['mark_adjustment'] == components['pnl']
    assert live['basis']['is_estimate'] is True and live['basis']['value_kind'] == 'THIRD_PARTY_ESTIMATE'
    assert live['quote']['source'] == 'Synthetic live'
    assert '不再重复' in live['price_notice'] and rows(wb) == before
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1.02'})
    formal = wb.valuation_explanation(wb.fid)
    assert formal['basis']['is_estimate'] is False and formal['basis']['value_kind'] == 'OFFICIAL_NAV'
    assert formal['components']['pnl'] == D('20') and formal['components']['mark_adjustment'] == 0


def test_unknown_cost_keeps_portfolio_total_unknown_and_shows_known_subtotal(wb):
    opening(wb)
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1.1'})
    other = wb.save_fund({'fund_code': '099999', 'name': 'Synthetic unknown cost', 'share_class': 'C'})
    wb.event('OPENING_LOT', {'fund_id': other['id'], 'lot_id': 'unknown-cost-lot', 'units': '10', 'cost': None,
                            'acquired': '2026-06-01', 'available_from': '2026-06-03'}, 'unknown-opening-1234', wb.now())
    wb.observe({'fund_id': other['id'], 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '2'})
    before = rows(wb)
    result = wb.valuation_explanation()
    assert result['fund_pnl'] is None and result['total_pnl'] is None and result['other_tracked_pnl'] is None
    assert result['known_count'] == 1 and result['fund_count'] == 2 and result['known_fund_pnl'] == D('100')
    unknown = next(f for f in result['funds'] if f['fund_id'] == other['id'])
    assert unknown['status'] == 'PARTIAL' and unknown['components']['pnl'] is None
    assert rows(wb) == before


def test_sale_fees_and_correction_are_explanations_without_second_deduction(wb):
    opening(wb, '100', '100')
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1.2'})
    oid = order(wb, 'SELL', '10', bucket='TACTICAL')
    confirmation = confirm(wb, oid, '10', '1.2', '12', '.2', '11.8')
    with wb.store.connect() as c:
        old = next(e for e in Store.events(c) if e['id'] == confirmation['id'])
    replacement = dict(old['payload'], fee='.3', net='11.7')
    wb.correct({'event_id': old['id'], 'reason': 'Synthetic actual channel fee correction',
                'expected_revision': wb.snapshot()['revision'], 'occurred_at': wb.now(), 'key': 'test-fee-000000',
                'replacement': {'kind': 'ORDER_CONFIRMED', 'payload': replacement, 'occurred_at': old['occurred_at']}})
    before = rows(wb)
    result = wb.valuation_explanation(wb.fid)
    assert result['correction_count'] == 1 and result['recorded_sale_fees'] == D('.3')
    assert any(e['kind'] == 'REVERSAL' for e in result['recent_events'])
    assert result['components']['pnl'] == D('19.70')
    assert result['components']['realized'] == D('1.70')
    assert '不再' in result['fee_scope'] and rows(wb) == before


def test_pre_disclosure_close_is_frozen_then_resolved_and_repeated_collection_is_bounded(wb):
    wb.now = lambda: '2026-10-20T14:00:00Z'
    official(wb, '2026-10-19', '1')
    quote_id = close(wb)
    assert wb.capture_estimate_results()['changed'] == 1
    pending = receipt(wb)
    assert pending['status'] == 'PENDING' and pending['formal_first'] is None and pending['first_comparison'] is None
    assert pending['estimate']['id'] == quote_id
    first_id = official(wb)
    assert wb.capture_estimate_results()['changed'] == 1
    saved = receipt(wb)
    assert saved['status'] == 'RESOLVED' and saved['formal_first']['id'] == first_id
    assert D(saved['first_comparison']['change_error_pp']) == D('1')
    for _ in range(3):
        assert close(wb) == quote_id
        assert wb.capture_estimate_results()['changed'] == 0
        assert receipt(wb) == saved
    with wb.store.connect() as c:
        assert len(Store.docs(c, DEVIATION_KIND)) == 1


@pytest.mark.parametrize('bad', ['late_receipt', 'intraday', 'lunch', 'unverified_provider', 'manual_quality'])
def test_late_or_ineligible_close_never_hindsight_fills_accuracy(wb, bad):
    wb.now = lambda: '2026-10-20T14:00:00Z'
    official(wb, '2026-10-19', '1'); official(wb)
    args = {'observed': '2026-10-20T13:00:00Z'} if bad == 'late_receipt' else {}
    if bad == 'intraday': args.update(as_of='2026-10-20T06:30:00Z', phase='LIVE')
    if bad == 'lunch': args.update(as_of='2026-10-20T03:30:00Z', phase='SOURCE_LUNCH')
    if bad == 'unverified_provider': args.update(provider='UNVERIFIED_SOURCE')
    if bad == 'manual_quality': args.update(quality='MANUAL')
    close(wb, **args)
    assert wb.capture_estimate_results()['changed'] == 0 and receipt(wb) is None


def test_manual_nav_is_not_a_first_formal_result(wb):
    wb.now = lambda: '2026-10-20T14:00:00Z'
    official(wb, '2026-10-19', '1'); close(wb)
    official(wb, quality='MANUAL')
    wb.capture_estimate_results()
    saved = receipt(wb)
    assert saved['status'] == 'PENDING' and saved['formal_first'] is None


def test_known_publication_time_is_stricter_than_first_formal_retrieval(wb):
    wb.now = lambda: '2026-10-20T14:00:00Z'
    official(wb, '2026-10-19', '1'); close(wb)
    wb.observe({'fund_id':wb.fid,'value_kind':'OFFICIAL_NAV','effective_date':'2026-10-20','value':'1.01',
        'observed_at':'2026-10-20T12:00:00Z','published_at':'2026-10-20T06:50:00Z',
        'source':'Synthetic manager with known publication','quality':'MANAGER_DISCLOSURE'})
    assert wb.capture_estimate_results()['changed']==0 and receipt(wb) is None


def test_compact_pending_evidence_survives_quote_cache_removal(wb):
    wb.now = lambda: '2026-10-20T14:00:00Z'
    official(wb, '2026-10-19', '1')
    quote_id = close(wb)
    wb.capture_estimate_results()
    with wb.store.transaction() as c:
        c.execute('DELETE FROM observations WHERE id=?', (quote_id,))
    official(wb)
    assert wb.capture_estimate_results()['changed'] == 1
    saved = receipt(wb)
    assert saved['estimate']['id'] == quote_id and saved['status'] == 'RESOLVED'
    assert D(saved['first_comparison']['change_error_pp']) == 1


def test_formal_revisions_preserve_first_result_and_update_latest_only(wb):
    wb.now = lambda: '2026-10-20T14:00:00Z'
    official(wb, '2026-10-19', '1'); close(wb); first_id = official(wb)
    wb.capture_estimate_results(); first = receipt(wb)
    new_id = official(wb, value='1.03', observed='2026-10-20T13:00:00Z')
    assert wb.capture_estimate_results()['changed'] == 1
    latest = receipt(wb)
    assert latest['formal_first']['id'] == first_id and latest['formal_latest']['id'] == new_id
    assert latest['first_comparison'] == first['first_comparison']
    assert D(latest['latest_comparison']['change_error_pp']) == D('-1')
    assert wb.capture_estimate_results()['changed'] == 0


def test_nonterminating_ratio_is_a_readable_decimal_not_an_invalid_money_input(wb):
    wb.now = lambda: '2026-10-20T14:00:00Z'
    official(wb, '2026-10-19', '1.30'); close(wb, value='1.43', reference_nav='1.30'); official(wb, value='1.40')
    wb.capture_estimate_results()
    result = wb.valuation_explanation(wb.fid)['estimate_results']
    assert result['resolved'] == 1 and result['scored_count'] == 1
    assert abs(result['mae_pp'] - D('2.307692307692')) < D('.000000000001')


def test_each_fund_retains_at_most_thirty_dates_and_get_does_not_capture(wb):
    wb.now = lambda: '2026-10-23T14:00:00Z'
    start = date(2026, 9, 20)
    for i in range(32):
        day = (start + timedelta(days=i)).isoformat()
        close(wb, day, reference_date=(start + timedelta(days=i-1)).isoformat())
    wb.capture_estimate_results()
    with wb.store.connect() as c:
        saved = Store.docs(c, DEVIATION_KIND)
    assert len(saved) == 30 and min(r['date'] for r in saved) == '2026-09-22'
    before = rows(wb)
    for _ in range(2):
        result = wb.valuation_explanation(wb.fid)
        assert result['estimate_results']['pending'] == 30
    assert rows(wb) == before


def test_explanation_api_requires_session_is_readonly_and_mode_isolated(wb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    opening(wb)
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1.1'})
    app = create_app(wb.root, testing=True)
    app.state.workbenches['real'].now = wb.now
    with TestClient(app) as client:
        path = f'/api/v1/funds/{wb.fid}/valuation-explanation'
        assert client.get(path+'?mode=real').status_code == 401
        client.get('/api/v1/session'); before = rows(wb)
        response = client.get(path+'?mode=real')
        assert response.status_code == 200 and response.json()['components']['pnl'] == '100.00'
        assert client.get('/api/v1/valuation-explanation?mode=real').json()['known_count'] == 1
        assert client.get(path+'?mode=demo').json()['code'] == 'FUND_NOT_FOUND'
        assert client.get(path+'?mode=invalid').status_code == 400
        assert rows(wb) == before
