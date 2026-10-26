import { useEffect, useState } from 'react';
import type { Row } from './api';
import { cash } from './api';
import { dailyAgendaMatchesSnapshot } from './dailyAgendaData';
import './portfolioRisk.css';

const share = (value: unknown) => value == null ? '—' : `${(Number(value) * 100).toFixed(1)}%`;
const coefficient = (value: unknown) => value == null ? '—' : Number(value).toFixed(2);

// Match identities, never recompute money or a recommendation in the browser.
export function portfolioRiskMatchesSnapshot(state?: Row | null, value?: Row | null): boolean {
  if (!dailyAgendaMatchesSnapshot(state, value) || !state || !value ||
      (state.fund_sidebar?._version ?? 0) !== value.sidebar_version) return false;
  const rows = new Map<string, Row>(value.snapshot_basis.map((row: Row) => [row.fund_id, row]));
  return state.funds.every((fund: Row) => {
    const row = rows.get(fund.id), valuation = fund.return_valuation || {};
    return !!row && ['nav_id', 'valuation_quote_id', 'holding_value'].every(key => Object.prototype.hasOwnProperty.call(row, key)) &&
      (row.nav_id ?? null) === (fund.nav?.id ?? null) &&
      (row.valuation_quote_id ?? null) === (valuation.quote_id ?? null) &&
      (row.holding_value ?? null) === (valuation.holding_value ?? null);
  });
}

export function PortfolioRisk({value, onSelectFund}: {value?: Row; onSelectFund?: (id: string) => void}) {
  const [clock, setClock] = useState(Date.now);
  const expiry = value?.daily_operations?.expires_at;
  useEffect(() => {
    if (!expiry) return;
    const at = Date.parse(expiry), now = Date.now();
    if (at <= now) return;
    const timer = window.setTimeout(() => setClock(Date.now()), at - now + 1);
    return () => window.clearTimeout(timer);
  }, [expiry]);
  return <PortfolioRiskContent value={value} onSelectFund={onSelectFund} now={Math.max(clock, Date.now())}/>;
}

export function PortfolioRiskContent({value, onSelectFund, now=Date.now()}: {value?: Row; onSelectFund?: (id: string) => void; now?: number}) {
  if (!value) return null;
  const allocation = value.allocation || {}, operations = value.daily_operations || {}, correlations = value.correlations || {};
  const groups: Row[] = allocation.groups || [], pairs: Row[] = correlations.pairs || [];
  const expired = !!operations.expires_at && Date.parse(operations.expires_at) <= now;
  const fundName = (id: string) => (allocation.funds || []).find((fund: Row) => fund.fund_id === id)?.name || '基金';
  const name = (id: string, label: string) => onSelectFund ? <button className="risk-fund" onClick={() => onSelectFund(id)}>{label}</button> : label;
  return <details className="portfolio-risk panel">
    <summary><span className="risk-title">组合风险</span><span>{allocation.holding_count || 0} 只持仓{allocation.largest_group ? ` · 最大分类 ${allocation.largest_group.name} ${share(allocation.largest_group.weight)}` : allocation.status === 'PARTIAL' ? ' · 估值待补' : ''}</span></summary>
    <div className="risk-content">
      <section className="risk-allocation" aria-label="分类集中度">
        <div className="risk-section-heading"><h3>分类集中度</h3><span>{allocation.status === 'PARTIAL' ? `已知市值 ¥${cash(allocation.known_subtotal)}` : `App总持仓 ¥${cash(allocation.total_value)}`}</span></div>
        {groups.length > 0 ? <div className="risk-groups">{groups.map((group, index) => <div className="risk-group" key={group.group_id}>
          <div><strong>{group.name}</strong><span>{group.value == null ? `已知 ¥${cash(group.known_subtotal)} · 尚有估值待补` : `¥${cash(group.value)}`}</span><strong>{group.weight == null ? '比例待补' : share(group.weight)}</strong></div>
          {group.weight != null && <div className="risk-bar" aria-label={`${group.name} ${share(group.weight)}`}><i className={index % 2 ? 'khaki' : 'blue'} style={{width:`${Math.min(100, Math.max(0, Number(group.weight) * 100))}%`}}/></div>}
          <div className="risk-members">{group.fund_ids.map((id: string) => <span key={id}>{name(id, fundName(id))}</span>)}</div>
        </div>)}</div> : <p>暂无已确认持仓。</p>}
        {allocation.status === 'PARTIAL' && <p>还有 {allocation.missing_fund_ids?.length || 0} 只持仓估值待补，整体总额与占比暂不计算。</p>}
        <p className="risk-note">{value.notice}</p>
      </section>
      <section className="risk-operations" aria-label="当日操作合计">
        <div className="risk-section-heading"><h3>当日操作合计</h3><span>仅合计当前可执行参考</span></div>
        {expired ? <p>参考已过期，刷新后重新计算合计。</p> : operations.action_count > 0 ? <>
          <div className="risk-operation-grid"><div><span>参考买入 · {operations.buy_count} 只</span><strong>¥{cash(operations.buy_amount)}</strong></div><div><span>参考卖出 · {operations.sell_count} 只</span><strong>¥{cash(operations.sell_gross)}</strong></div><div><span>预计卖出净到账</span><strong>¥{cash(operations.sell_net)}</strong></div></div>
          <p>预计费用 ¥{cash(operations.estimated_fees)}；买入减预计卖出净到账 ¥{cash(operations.net_subscription)}。</p>
          <p className="risk-note">这是同一时点各基金参考的汇总，未假设回款可立即复用，不要求登记你的现金，也不会自动下单。</p>
        </> : <p>当前没有可执行参考；午休、截止及过期参考不计入。</p>}
      </section>
      <section className="risk-correlations" aria-label="相近走势">
        <div className="risk-section-heading"><h3>相近走势</h3><span>{correlations.ready_count || 0} 组资料就绪</span></div>
        {pairs.some(pair => pair.status === 'READY') ? <div className="risk-pair-list">{pairs.filter(pair => pair.status === 'READY').slice(0, 5).map(pair => <div className="risk-pair" key={pair.fund_ids.join(':')}>
          <div>{name(pair.fund_ids[0], pair.names[0])}<span> / </span>{name(pair.fund_ids[1], pair.names[1])}<small>{pair.sample_count} 个相同区间 · {pair.start} 至 {pair.end}</small></div><div><strong>{coefficient(pair.correlation)}</strong><span>{pair.label}</span></div>
        </div>)}</div> : <p>暂没有满足至少 {correlations.minimum_samples || 20} 个相同收益区间的组合。</p>}
        {pairs.length > 0 && <details className="risk-history"><summary>全部走势比较与依据</summary>{pairs.map(pair => <div className="risk-pair" key={pair.fund_ids.join(':')}><div>{pair.names.join(' / ')}<small>{pair.status === 'READY' ? `${pair.sample_count} 期 · ${pair.start} 至 ${pair.end}` : pair.fund_ids.map((id: string) => `${fundName(id)}：${correlations.history?.[id]?.label || '待核对'}`).join('；')}</small></div><div><strong>{coefficient(pair.correlation)}</strong><span>{pair.label}</span></div></div>)}</details>}
        <p className="risk-note">{correlations.notice}“高度同向”表示相关系数至少 0.80，不作风险评级。</p>
      </section>
    </div>
  </details>;
}
