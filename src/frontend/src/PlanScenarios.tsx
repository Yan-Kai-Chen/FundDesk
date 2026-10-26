import { useEffect, useRef, useState } from 'react';
import { api, cash, pct, quantity } from './api';
import type { Row } from './api';
import './planScenarios.css';

function currentBasis(fund: Row, revision?: number) {
  return JSON.stringify({nav_id: fund.nav?.id ?? null,
    profile_version: fund.investment_plan_profile?._version ?? 0,
    rule_version: fund.rules?._version ?? 0, units: String(fund.units ?? ''), revision,
    buckets: fund.position_buckets?.groups});
}

export function PlanScenariosResult({value}: {value: Row}) {
  const [selected, setSelected] = useState(value.scenarios?.[0]?.id ?? '');
  const scenario = value.scenarios?.find((item: Row) => item.id === selected) || value.scenarios?.[0];
  if (!scenario) return <p className="plan-scenarios-note">{value.reason}</p>;
  return <div className="plan-scenarios-result">
    <p className="plan-scenarios-note">从 {value.initial_nav_date} 正式净值 {quantity(value.initial_nav, 4)} 起，路径尺度 h = {pct(value.h).replace(/^\+/, '')}。以下为假设参考期，不是今日实况。</p>
    <div className="plan-scenarios-tabs" role="group" aria-label="选择固定情景">{value.scenarios.map((item: Row) =>
      <button type="button" key={item.id} className={item.id === scenario.id ? 'active' : ''} onClick={() => setSelected(item.id)}>{item.label}</button>)}</div>
    <div className="plan-scenarios-summary">
      <div><span>情景期末资产</span><strong>{scenario.ending_assets == null ? '—' : `¥${cash(scenario.ending_assets)}`}</strong></div>
      <div><span>情景期间盈亏</span><strong>{scenario.period_pnl == null ? '—' : `¥${cash(scenario.period_pnl)}`}</strong></div>
      <div><span>新增情景投入</span><strong>¥{cash(scenario.external_contributions)}</strong></div>
      <div><span>申请预计费用</span><strong>¥{cash(scenario.estimated_fees)}</strong></div>
    </div>
    <p className="plan-scenarios-note">{scenario.reason}</p>
    {scenario.existing_in_flight_count > 0 && <p className="plan-scenarios-note">已有 {scenario.existing_in_flight_count} 笔在途不模拟确认，完整资产与损益留空。已确认及新增情景范围：资产 ¥{cash(scenario.known_scope_assets)}，期间盈亏 ¥{cash(scenario.known_scope_period_pnl)}。</p>}
    <div className="plan-scenarios-table"><table><thead><tr><th>参考期</th><th>涨跌</th><th>买卖参考</th><th>费用</th><th>T 份额</th><th>计划占用</th><th>条件</th></tr></thead><tbody>
      {scenario.rows.map((row: Row) => <tr key={row.period}><td>第 {row.period} 期</td><td>{pct(row.change)}</td>
        <td>{row.amount == null ? '—' : row.action === 'BUY' || row.action === 'SELL' ? `${row.action === 'BUY' ? '买入' : '卖出'} ¥${cash(row.amount)}` : '不操作'}{row.simulated && <span className="plan-scenarios-tag">假设申请</span>}</td>
        <td>{row.fee == null ? '—' : `¥${cash(row.fee)}`}</td><td>{quantity(row.t_units)}{row.t_available_units != null && <span className="plan-scenarios-tag">可卖 {quantity(row.t_available_units)}</span>}</td><td>{row.capital_used == null ? '—' : `¥${cash(row.capital_used)}`}</td>
        <td>{row.simulated ? `${row.risk_state || '满足当前计划'}；按申请期净值、后续确认` : row.reason || '无操作参考'}</td></tr>)}
    </tbody></table></div>
    <details className="plan-scenarios-detail"><summary>假设与计算日期</summary><ul>{value.assumptions.map((line: string, index: number) => <li key={index}>{line}</li>)}</ul>
      <p>{value.calendar_label}</p><div className="plan-scenarios-table"><table><thead><tr><th>参考期</th><th>日历映射</th><th>申请净值日</th><th>确认日</th><th>可卖日</th><th>T 仓市值</th><th>本笔预计实现</th></tr></thead><tbody>
        {scenario.rows.map((row: Row) => <tr key={row.period}><td>第 {row.period} 期</td><td>{row.date || '未核实'}</td><td>{row.trade_date || '—'}</td><td>{row.confirm_date || '—'}</td><td>{row.available_from || '—'}</td><td>¥{cash(row.t_value)}</td><td>{row.expected_realized_pnl == null ? '—' : `¥${cash(row.expected_realized_pnl)}`}</td></tr>)}
      </tbody></table></div><p>期末待确认买入 ¥{cash(scenario.pending_buy)}；虚拟赎回应收 ¥{cash(scenario.virtual_receivable)}。应收不代表已到账现金。{scenario.cumulative_pnl != null && `同情景价下原基金累计盈亏 ¥${cash(scenario.cumulative_pnl)}，与期间盈亏分开，不重复相加。`}</p>
    </details>
  </div>;
}

export function PlanScenarios({fund, mode, revision}: {fund: Row; mode: string; revision?: number}) {
  const [result, setResult] = useState<Row | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const basis = currentBasis(fund, revision), latest = useRef(basis), request = useRef(0);
  latest.current = basis;
  useEffect(() => {request.current++; setResult(null); setError(''); setBusy(false);}, [basis, mode, fund.id]);
  useEffect(() => () => {request.current++;}, []);
  const enabled = fund.investment_plan_profile?.enabled === true;
  const run = async () => {
    const token = ++request.current, captured = basis;
    setBusy(true); setError('');
    try {
      const value = await api(`/funds/${fund.id}/plan-scenarios`, mode, {expected_version: fund.investment_plan_profile?._version ?? 0, expected_nav_id: fund.nav?.id ?? null});
      if (request.current === token && latest.current === captured) {
        if (value.basis && (String(value.basis.units) !== String(fund.units) ||
          value.basis.rule_version !== (fund.rules?._version ?? 0) || (revision != null && value.revision !== revision))) {
          setError('持仓或规则已变化，请刷新后重新预演。');
        } else setResult(value);
      }
    } catch (err) {if (request.current === token) setError((err as Error).message);}
    finally {if (request.current === token) setBusy(false);}
  };
  return <details className="panel plan-scenarios"><summary>计划情景预演<span className="pill khaki">固定路径</span></summary><div className="plan-scenarios-body">
    <p>检查连续下跌、先跌后涨和持续上涨时，当前计划如何控制买卖、费用与仓位。点击后计算，结果不保存、不下单。</p>
    {!enabled ? <p>尚未启用投资计划；这里不会自动启用或填写总预算。</p> : <button type="button" className="button" onClick={run} disabled={busy}>{busy ? '预演中…' : result ? '重新预演' : '预演三种情景'}</button>}
    {error && <p role="alert">{error}</p>}{result && <PlanScenariosResult value={result}/>}
  </div></details>;
}
