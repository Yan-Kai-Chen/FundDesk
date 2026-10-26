import pytest

from app.domain.finance import Problem
from app.persistence.store import Store, pack
from app.services.plan_scenarios import PlanScenariosMixin
from conftest import opening


def attach(wb):
    wb.plan_scenarios = PlanScenariosMixin.plan_scenarios.__get__(wb)
    return wb


def signature(wb):
    with wb.store.connect() as c:
        tables = [r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {name: [tuple(r) for r in c.execute('SELECT * FROM ' + name + ' ORDER BY 1')] for name in tables}


def test_explicit_preview_is_read_only_and_disabled_does_not_create_a_profile(wb):
    attach(wb); opening(wb)
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1'})
    nav = next(f for f in wb.snapshot()['funds'] if f['id'] == wb.fid)['nav']
    before = signature(wb)
    result = wb.plan_scenarios(wb.fid, {'expected_version': 0, 'expected_nav_id': nav['id']})
    assert result['status'] == 'PLAN_DISABLED' and result['ledger_posted'] is False
    assert signature(wb) == before


def test_active_plan_preview_never_writes_real_or_demo_tables(wb):
    attach(wb); opening(wb)
    wb.observe({'fund_id': wb.fid, 'value_kind': 'OFFICIAL_NAV', 'effective_date': '2026-10-20', 'value': '1'})
    with wb.store.transaction() as c:
        Store.put(c, 'investment_plan_profile', wb.fid, {'enabled': True, 'holding_cap': '2000', 'template': 'DAILY_LADDER_V1'})
    current = wb.snapshot(); f = next(f for f in current['funds'] if f['id'] == wb.fid)
    before = signature(wb)
    value = wb.plan_scenarios(wb.fid, {'expected_version': 1, 'expected_nav_id': f['nav']['id']})
    assert len(value['scenarios']) == 3 and value['revision'] == current['revision']
    assert signature(wb) == before
    assert 'NaN' not in pack(value)


def test_stale_plan_or_nav_and_unbounded_custom_paths_are_rejected(wb):
    attach(wb)
    with pytest.raises(Problem) as stale:
        wb.plan_scenarios(wb.fid, {'expected_version': 1, 'expected_nav_id': None})
    assert stale.value.code == 'VERSION_CONFLICT'
    with pytest.raises(Problem) as invalid:
        wb.plan_scenarios(wb.fid, {'expected_version': 0, 'expected_nav_id': None, 'path': ['999'] * 10000})
    assert invalid.value.code == 'INVALID_PLAN_SCENARIOS'
