"""Explicit, read-only scenario requests; never run from state/GET/schedulers."""
from app.domain.finance import require
from app.domain.plan_scenarios import plan_scenarios as calculate_plan_scenarios


class PlanScenariosMixin:
    def plan_scenarios(self, fund_id, p):
        require(set(p) == {'expected_version', 'expected_nav_id'}, 'INVALID_PLAN_SCENARIOS',
                '预演仅使用现有计划和三条固定路径，请刷新后重试。')
        require(type(p['expected_version']) is int and p['expected_version'] >= 0 and
                (p['expected_nav_id'] is None or isinstance(p['expected_nav_id'], str)),
                'INVALID_PLAN_SCENARIOS', '请提供当前计划与正式净值起点。')
        with self.store.connect() as c:
            snapshot = self.snapshot(c)
            fund = next((f for f in snapshot['funds'] if f['id'] == fund_id), None)
            require(fund is not None, 'FUND_NOT_FOUND', '基金不存在。')
            require((fund.get('investment_plan_profile') or {}).get('_version', 0) == p['expected_version'] and
                    (fund.get('nav') or {}).get('id') == p['expected_nav_id'],
                    'VERSION_CONFLICT', '计划或正式净值已变化，请刷新后重新预演。')
            result = calculate_plan_scenarios(fund, snapshot['orders'], snapshot['now'])
            result['revision'] = snapshot['revision']
            result['basis'] = {
                'nav_id': (fund.get('nav') or {}).get('id'),
                'profile_version': (fund.get('investment_plan_profile') or {}).get('_version', 0),
                'rule_version': (fund.get('rules') or {}).get('_version', 0),
                'units': fund.get('units'),
            }
            return result
