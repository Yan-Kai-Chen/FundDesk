"""Explicit comparison requests; scheduled maintenance freezes at a clean start."""
from app.domain.finance import ZERO, dec, money, require
from app.domain.t_review import POLICY, POLICY_VERSION, prefix_fingerprint, review_quote, t_review
from app.persistence.store import Store


def _pending(state, fund_id):
    return [o['id'] for o in state['orders'].values() if o['fund_id'] == fund_id and
            (not o['closed'] or o.get('unsettled', ZERO) or o.get('refund_due', ZERO))]


def _can_freeze(fund):
    return (review_quote(fund)['value'] is not None and not fund.get('initialization_pending') and
            (fund.get('position_buckets') or {}).get('status') != 'BLOCKED')


def _freeze(fund, state, at, doc):
    quote, units = review_quote(fund), dec(fund.get('units', ZERO))
    return dict(doc, start_pending=False, started_at=at, revision=state['revision'],
                units=units, nav=quote['value'], nav_date=quote['date'], quote_id=quote.get('quote_id'),
                is_estimate=quote['is_estimate'], holding_value=money(units * quote['value']),
                committed_order_ids=[], prefix_fingerprint=prefix_fingerprint(state, fund['id'], state['revision']))


class TReviewMixin:
    def t_review_context(self, c, fund, state):
        return t_review(fund, state, Store.doc(c, 't_review_baseline', fund['id']))

    def t_review(self, fund_id):
        snapshot = self.snapshot()
        fund = next((f for f in snapshot['funds'] if f['id'] == fund_id), None)
        require(fund is not None, 'FUND_NOT_FOUND', '基金不存在')
        with self.store.connect() as c:
            state = self.state(c)
            require(state['revision'] == snapshot['revision'], 'VERSION_CONFLICT', '持仓已变化，请刷新重试')
            return self.t_review_context(c, fund, state)

    def start_t_review(self, fund_id, p):
        require(set(p) <= {'expected_revision', 'expected_version', 'policy', 'accept_scenario'}, 'INVALID_T_REVIEW', '比较基准包含未知字段')
        require(p.get('policy') == POLICY and p.get('accept_scenario') is True, 'T_REVIEW_ACCEPTANCE_REQUIRED', '请明确接受资金复用情景的范围与假设')
        require(type(p.get('expected_revision')) is int and type(p.get('expected_version')) is int, 'INVALID_T_REVIEW', '请提供当前账本与基准版本')
        snapshot = self.snapshot()
        fund = next((f for f in snapshot['funds'] if f['id'] == fund_id), None)
        require(fund is not None, 'FUND_NOT_FOUND', '基金不存在')
        require(_can_freeze(fund), 'T_REVIEW_HOLDINGS_REQUIRED', '需先有可核对持仓和净值；分红或拆分后请先核对持仓')
        with self.store.transaction() as c:
            state = self.state(c)
            require(state['revision'] == snapshot['revision'] == p['expected_revision'], 'VERSION_CONFLICT', '持仓已变化，请刷新后重新建立基准')
            old = Store.doc(c, 't_review_baseline', fund_id)
            require(p['expected_version'] == (old['_version'] if old else 0), 'VERSION_CONFLICT', '基准已变化，请刷新重试')
            history = list((old or {}).get('previous', []))
            if old and not old.get('start_pending'):
                history.append({k: old.get(k) for k in ('started_at', 'units', 'nav', 'nav_date', 'holding_value', 'revision')})
            pending, now = _pending(state, fund_id), self.now()
            doc = {'schema_version': 1, 'policy': POLICY, 'policy_version': POLICY_VERSION,
                   'fund_id': fund_id, 'requested_at': now, 'requested_revision': state['revision'],
                   'start_pending': bool(pending), 'started_at': None, 'committed_order_ids': pending,
                   'previous': history[-5:]}
            if not pending:
                doc = _freeze(fund, state, now, doc)
            Store.put(c, 't_review_baseline', fund_id, doc, p['expected_version'])
            return self.t_review_context(c, fund, state)

    def t_review_maintenance(self, c=None):
        """Call after automatic bookkeeping, never from a read-only projection.

        A waiting request is written once, then freezes once after *all* orders
        for that fund have completed, including orders submitted while waiting.
        No transaction events or repeated waiting snapshots are created.
        """
        if c is None:
            with self.store.transaction() as connection:
                return self.t_review_maintenance(connection)
        waiting = [d for d in Store.docs(c, 't_review_baseline') if d.get('start_pending')]
        result = {'started': [], 'waiting': []}
        if not waiting:
            return result
        state = self.state(c)
        eligible = []
        for doc in waiting:
            pending = _pending(state, doc['fund_id'])
            if pending:
                result['waiting'].append({'fund_id': doc['fund_id'], 'order_count': len(pending), 'reason': 'WAIT_ORDERS'})
            else:
                eligible.append(doc)
        if not eligible:
            return result
        funds = {f['id']: f for f in self.snapshot(c)['funds']}
        for doc in eligible:
            fund = funds.get(doc['fund_id'])
            if fund is None or not _can_freeze(fund):
                result['waiting'].append({'fund_id': doc['fund_id'], 'order_count': 0, 'reason': 'WAIT_HOLDINGS_OR_QUOTE'})
                continue
            frozen = _freeze(fund, state, self.now(), doc)
            frozen['awaited_order_ids'] = doc['committed_order_ids']
            Store.put(c, 't_review_baseline', fund['id'], frozen, doc['_version'])
            result['started'].append({'fund_id': fund['id'], 'started_at': frozen['started_at'], 'revision': state['revision']})
        return result
