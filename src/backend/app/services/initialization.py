"""An explicitly accepted reconstructed opening, separate from channel confirmations."""
from decimal import Decimal, ROUND_HALF_UP

from app.domain.finance import ZERO, amount, dec, money, require
from app.persistence.store import Store, digest
from app.services.t_attribution import reconstruct


def split_opening(units, cost, nav, base_value, at):
    units, cost, nav, target = dec(units, positive=True), amount(cost), dec(nav, positive=True), amount(base_value)
    require(target <= money(units * nav), 'BASE_VALUE_EXCESS', '底仓金额不能超过当前持仓市值')
    base_units = min(units, (target / nav).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    if target == money(units * nav):
        base_units = units
    base_cost = cost if base_units == units else money(cost * base_units / units)
    return [{'id': 'opening-base', 'bucket': 'BASE', 'units': base_units, 'cost': base_cost, 'at': at},
            {'id': 'opening-t', 'bucket': 'TACTICAL', 'units': units-base_units, 'cost': cost-base_cost, 'at': at}]


class InitializationMixin:
    def tracking_proposal(self, c, fund_id, p):
        self.fund(c, fund_id)
        state = self.state(c)
        require(not any(l['fund_id'] == fund_id for l in state['lots'].values()) and
                not any(o['fund_id'] == fund_id for o in state['orders'].values()) and fund_id not in state['tracking'],
                'HISTORY_OVERLAP', '此基金已建账，不能重复初始化覆盖同一历史')
        statement = Store.doc(c, 'statement', fund_id)
        require(statement is not None, 'STATEMENT_REQUIRED', '请先保存完整交易资料')
        rule = self.rules(c, fund_id)
        history = self.observations(c, fund_id, 'OFFICIAL_NAV')
        analysis = self.bucket_analysis(c, fund_id, history, strict=True)
        require(analysis['classification_saved'], 'BUCKET_CONFIG_REQUIRED', '请先保存历史买卖归属')
        ref = reconstruct(statement, history, rule)
        cost = sum((g['cost'] for g in analysis['groups'].values()), ZERO)
        positions = split_opening(ref['units'], cost, ref['nav'], p['base_value'], self.now())
        physical = [dict(l, id='tracking-'+digest([fund_id,l['id']])[:20]) for l in ref['remaining_lots']]
        historical = [dict(r, side='BUY') for r in ref['buys']] + [dict(r, side='SELL') for r in ref['sells']]
        payload = {'fund_id': fund_id, 'basis': 'USER_ACCEPTED_RECONSTRUCTION',
            'source_basis': '用户要求按完整历史与公开净值开始跟踪，并从当前持仓直接划分底仓；推算字段保留估算标记',
            'statement_version': statement['_version'], 'rule_version': rule['_version'],
            'classification_version': analysis['classification_version'], 'nav_observation_id': ref['observation_id'],
            'nav': ref['nav'], 'nav_date': ref['nav_date'], 'base_value': amount(p['base_value']),
            'units': ref['units'], 'holding_value': ref['holding_value'], 'economic_cost': cost,
            'net_invested': ref['paid']-ref['received'], 'fifo_realized': ref['fifo_realized'],
            'historical_paid': ref['paid'], 'historical_redemptions': ref['received'],
            'historical_pending_redemptions': sum((s['net'] for s in ref['sells'] if s['pending']), ZERO),
            'physical_lots': physical, 'economic_positions': positions,
            'bucket_realized': {k:g['realized'] for k,g in analysis['groups'].items()},
            'historical_sales': [dict(s,reconstructed=True) for s in analysis['sales']], 'historical_rows': sorted(historical,key=lambda r:r['at']),
            'cash_scope': 'POST_START_ONLY', 'cash_notice': '只跟踪开始后的登记现金；此前赎回款不推定为当前可用现金',
            'total_pnl': ref['total_pnl'], 'source_hash': digest([statement, rule, history, analysis['assignments']])}
        # Exclude wall-clock presentation fields from the preview/commit token.
        token_payload = dict(payload, economic_positions=[{k:v for k,v in r.items() if k != 'at'} for r in positions])
        return dict(payload, proposal_token=digest(token_payload), expected_revision=state['revision'])

    def preview_tracking(self, fund_id, p):
        with self.store.connect() as c:
            return self.tracking_proposal(c, fund_id, p)

    def initialize_tracking(self, fund_id, p):
        require(p.get('accept_reconstruction') is True, 'TRACKING_ACCEPTANCE_REQUIRED', '需明确采用现有资料还原作为跟踪起点')
        with self.store.transaction() as c:
            key = 'tracking-start:'+fund_id
            old = c.execute('SELECT id,payload FROM events WHERE key=?', (key,)).fetchone()
            if old:
                import json
                saved = json.loads(old['payload'])
                require(saved['proposal_token'] == p.get('proposal_token'), 'IDEMPOTENCY_CONFLICT', '已开始跟踪，不能用另一份起点重复初始化')
                return {'id':old['id'], 'duplicate':True, 'revision':self.state(c)['revision']}
            proposal = self.tracking_proposal(c, fund_id, p)
            require(p.get('proposal_token') == proposal['proposal_token'] and p.get('expected_revision') == proposal['expected_revision'],
                    'VERSION_CONFLICT', '交易资料、净值或账本已更新，请重新预览跟踪起点')
            proposal.pop('expected_revision')
            proposal['started_at'] = self.now()
            result = Store.append(c, 'TRACKING_OPENING', proposal, key, proposal['started_at'])
            self.state(c)  # Validate both physical and economic books before commit.
            old_strategy = Store.doc(c, 'strategy', fund_id)
            require(not old_strategy or not old_strategy.get('enabled'), 'STRATEGY_ACTIVE', '已有策略启用，请先核对初始化方式')
            clean = {'fund_id':fund_id, 'enabled':True, 'type':'MANUAL_BUCKETS',
                     'params':{'initial_base_value':proposal['base_value']}, 'anchor_seq':result['seq'], 'updated_at':self.now()}
            strategy = Store.put(c, 'strategy', fund_id, clean, old_strategy['_version'] if old_strategy else 0)
            Store.put(c, 'strategy_history', fund_id+':'+str(strategy['_version']), strategy)
            return dict(result, revision=self.state(c)['revision'])
