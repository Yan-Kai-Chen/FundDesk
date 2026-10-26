import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Settings2, X } from 'lucide-react';
import { api, cash, pct, quantity } from './api';
import type { Row } from './api';
import { SellImpact } from './SellImpact';
import { investmentPlanMatchesFund, investmentPlanRecord } from './viewData';
import './investmentPlan.css';

export function InvestmentPlanReference({fund, reference, onSettings, onOrder}: {
  fund: Row; reference?: Row; onSettings: () => void; onOrder: (target: Row) => void;
}) {
  const profile = fund.investment_plan_profile;
  const item = investmentPlanMatchesFund(fund, reference) ? reference : undefined;
  const plan = item?.plan;
  const risk = plan?.risk;
  const adaptive = profile.template === 'DRAWDOWN_GUARD_V2';
  const todayReady = fund.daily_t?.today?.status === 'READY' && fund.daily_t?.today?.quote != null;
  const target = investmentPlanRecord(fund, item);
  const directional = item?.action === 'BUY' || item?.action === 'SELL';
  return <div className="daily-reference investment-plan" aria-label="投资计划与今日参考">
    <div className="daily-label"><span>做 T 计划</span><button className="intensity-button khaki" onClick={onSettings}><Settings2 size={16}/>投资计划</button></div>
    <div className="investment-plan-cap"><span>持仓上限</span><strong>¥{cash(profile.holding_cap)}</strong><span className="pill sage">保留底仓</span>{adaptive && <span className={`pill ${risk?.state === 'NORMAL' ? 'sage' : 'khaki'}`}>{risk?.label || '等待风险资料'}</span>}</div>
    <strong className="daily-number">{directional && item?.amount != null ? `${item.label} ¥${cash(item.amount)}` : item?.label || '正在计算'}</strong>
    <div className="daily-caption">{item?.reason || '结合当天报价、可用 T 仓与已记录操作计算'}</div>
    {item?.units != null && directional && <div className="daily-caption">{quantity(item.units)} 份 · 预计费用 ¥{cash(item.fee)}</div>}
    {item?.reference_label && <div className="daily-caption"><span className="pill khaki">{item.reference_label}</span>{item.preview_only && ' · 仅供查看'}</div>}
    {item?.action === 'SELL' && item.sell_impact && <SellImpact value={item.sell_impact} compact/>}
    {adaptive && risk?.reasons?.length > 0 && <div className="investment-risk-note">{risk.reasons[0]}{risk.provisional && <span> · 含今日参考价</span>}</div>}
    {plan && <div className="investment-plan-space"><div><span>{adaptive ? '成本保护占用' : '当前占用'}</span><strong>¥{cash(plan.committed_holding)}</strong></div><div><span>{adaptive ? '成本预算空间' : '可加仓空间'}</span><strong>¥{cash(plan.headroom)}</strong></div></div>}
    {!adaptive && plan?.valuation_basis === 'OFFICIAL_NAV' && <div className="daily-caption">按{plan.valuation_date || '最近披露日'}正式净值估算占用</div>}
    {!adaptive && plan?.valuation_basis === 'CURRENT_QUOTE' && <div className="daily-caption">按本次参考净值估算占用</div>}
    {adaptive && <div className="daily-caption">持仓与待买按成本保护；跌价不会释放已投入本金。</div>}
    {target && <button className="small-primary investment-record" onClick={() => onOrder(target)}>记录已操作 <ArrowRight size={15}/></button>}
    {item?.action_hint && <p className="daily-caption">{item.action_hint}</p>}
    {adaptive && risk && <details className="investment-plan-details"><summary>多日风险与恢复条件</summary>
      <div className="investment-risk-grid"><div><span>触发幅度 h</span><strong>{pct(risk.h).replace(/^\+/, '')}</strong></div><div><span>连续下跌</span><strong>{!risk.ready || risk.streak == null ? '—' : `${risk.streak} 个交易日`}</strong></div><div><span>近 5 日涨跌</span><strong>{pct(risk.return_5)}</strong></div><div><span>近 20 日高点回撤</span><strong>{pct(risk.drawdown_20)}</strong></div></div>
      <div className="daily-caption">正式净值截至 {risk.input_basis?.history_end_date || risk.input_basis?.date || '待补'}{risk.provisional ? ' · 叠加今日参考价，正式披露后更新' : ''}</div>
      {risk.reasons?.length > 1 && <ul className="investment-risk-reasons">{risk.reasons.slice(1).map((reason: string, index: number) => <li key={index}>{reason}</li>)}</ul>}
      <dl className="investment-plan-budget"><div><dt>近 5 个交易日已用买入额度</dt><dd>¥{cash(risk.rolling_buy_used)} / ¥{cash(risk.rolling_buy_limit)}</dd></div><div><dt>滚动剩余额度</dt><dd>¥{cash(risk.rolling_buy_remaining)}</dd></div><div><dt>成本基准</dt><dd>¥{cash(risk.cost_basis)}</dd></div><div><dt>成本保护占用</dt><dd>¥{cash(risk.capital_used)}</dd></div><div><dt>成本预算空间</dt><dd>¥{cash(risk.capital_headroom)}</dd></div><div><dt>加仓冷却</dt><dd>{risk.cooldown_blocked ? `暂停至 ${risk.cooldown_until || '后续开放日'}` : '未触发'}</dd></div></dl>
      {risk.recovery_conditions?.length > 0 && <div className="investment-recovery"><strong>恢复与继续条件</strong><ul>{risk.recovery_conditions.map((condition: string, index: number) => <li key={index}>{condition}</li>)}</ul></div>}
      <p>风险状态与今日金额分开判断。无有效当日报价时保留已知风险，不生成可操作金额；达到恢复条件后仍需重新核对当日涨跌及剩余额度。</p>
    </details>}
    {plan && <details className="investment-plan-details"><summary>{adaptive ? '今日金额怎么算' : '阶梯与今日额度'}</summary>
      <table><thead><tr><th>当日涨跌绝对值</th><th className="align-right">当日累计原目标</th></tr></thead><tbody>
        {adaptive && !risk?.ready ? <tr><td colSpan={2}>历史资料不足，触发幅度待计算</td></tr> : <><tr><td>不足 {adaptive ? pct(risk?.h).replace(/^\+/, '') : '1%'}</td><td className="align-right">不操作</td></tr>
        {(plan.tiers || []).map((tier: Row, index: number) => <tr key={tier.trigger}><td>{adaptive ? pct(tier.trigger).replace(/^\+/, '') : `${quantity(Number(tier.trigger) * 100, 0)}%`}{index < plan.tiers.length - 1 ? ` 至不足 ${adaptive ? pct(plan.tiers[index + 1].trigger).replace(/^\+/, '') : `${quantity(Number(plan.tiers[index + 1].trigger) * 100, 0)}%`}` : ' 及以上'}</td><td className="align-right">¥{cash(tier.amount)}</td></tr>)}</>}
      </tbody></table>
      <dl className="investment-plan-budget">{adaptive && <div><dt>原阶梯目标</dt><dd>{todayReady && risk?.ready ? `¥${cash(plan.raw_target_today)}` : '等待资料'}</dd></div>}<div><dt>{adaptive ? '风险调整后目标' : '今日目标'}</dt><dd>{adaptive && !(todayReady && risk?.ready) ? '等待资料' : `¥${cash(plan.target_today)}`}</dd></div><div><dt>已记录买入 / 卖出</dt><dd>¥{cash(plan.used_buy)} / ¥{cash(plan.used_sell)}</dd></div><div><dt>当日剩余额度</dt><dd>¥{cash(plan.remaining_daily)}</dd></div>{adaptive && <div><dt>最终参考金额</dt><dd>{item?.amount == null ? '等待有效报价或条件' : `¥${cash(item.amount)}`}</dd></div>}</dl>
      {adaptive ? <p>先按 h / 2h / 3h 计算原目标，防御状态的买入减半，暂停状态不新增买入。再扣减当天有效操作，核对滚动额度、冷却与成本空间；卖出继续核对可卖 T 仓、实际费用与净收益门槛。</p> : <p>跌买、涨卖，同向操作扣减累计目标；双向合计每日最多使用上限的 15%。同日有反向操作时暂停金额参考。占用包含当前持仓与待确认买入，价格上涨超过上限时不会要求强卖。</p>}
      <p>仅建议卖出可用 T 仓中按渠道 FIFO 可免赎回费的部分；预计净实现收益须至少达到 2 元且不低于卖出额的 0.5%。午间或 15:00 后只查看当时情景。</p>
      <p>这是执行纪律参考，尚未证明盈利优势。当前底仓份额保持；不要求登记账户总现金，计划不会自动下单。“记录已操作”仅预填本地表单，需核对后保存。</p>
    </details>}
    {item?.action !== 'SELL' && item?.candidate_sell_impact && <details className="investment-plan-details"><summary>未达卖出条件的测算</summary><p>当前不建议卖出。以下仅解释候选金额的收益情况。</p><SellImpact value={item.candidate_sell_impact} compact/></details>}
  </div>;
}

export function InvestmentPlanForm({fund, mode, onClose, onSaved}: {
  fund: Row; mode: string; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const profile = fund.investment_plan_profile || {enabled: false, holding_cap: '4000', _version: 0};
  const [enabled, setEnabled] = useState(profile.enabled === true);
  const [holdingCap, setHoldingCap] = useState(String(profile.holding_cap || '4000'));
  const [template, setTemplate] = useState(profile.template || 'DAILY_LADDER_V1');
  const [version] = useState(profile._version);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const modal = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    modal.current?.querySelector<HTMLInputElement>('input[name="holding-cap"]')?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose();
      if (e.key === 'Tab') {
        const controls = Array.from(modal.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled)') || []);
        const first = controls[0], last = controls.at(-1);
        if (e.shiftKey && document.activeElement === first) {e.preventDefault(); last?.focus();}
        if (!e.shiftKey && document.activeElement === last) {e.preventDefault(); first?.focus();}
      }
    };
    document.addEventListener('keydown', key);
    return () => {document.removeEventListener('keydown', key); previous?.focus();};
  }, [busy, onClose]);
  const validCap = Number.isFinite(Number(holdingCap)) && Number(holdingCap) > 0;
  return <div className="modal-backdrop"><div className="modal wide investment-plan-form" role="dialog" aria-modal="true" aria-labelledby="investment-plan-title" ref={modal}>
    <div className="modal-header"><div><span className="eyebrow">{fund.name}</span><h2 id="investment-plan-title">投资计划</h2></div><button className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={22}/></button></div>
    <form onSubmit={async e => {e.preventDefault(); setBusy(true); setError(''); try {
      await api(`/funds/${fund.id}/investment-plan`, mode, {enabled, holding_cap: holdingCap, template, expected_version: version}, 'PUT');
      await onSaved();
    } catch (err) {setError((err as Error).message);} finally {setBusy(false);}}}>
      <div className="modal-body">
        <label className="check-field"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} disabled={busy}/>启用做 T 计划</label>
        <label className="field">计划持仓上限（元）<input name="holding-cap" type="number" min="0.01" step="0.01" value={holdingCap} onChange={e => setHoldingCap(e.target.value)} required disabled={busy}/><small>保留当前底仓份额；不自动买满，不需要登记银行卡或支付宝余额。</small></label>
        <label className="field">计划纪律<select value={template} onChange={e => setTemplate(e.target.value)} disabled={busy}><option value="DAILY_LADDER_V1">单日阶梯</option><option value="DRAWDOWN_GUARD_V2">多日防御</option></select></label>
        <div className="investment-plan-template"><strong>{template === 'DRAWDOWN_GUARD_V2' ? '多日防御' : '当日涨跌阶梯'}</strong><span className="pill sage">固定纪律</span><p>{template === 'DRAWDOWN_GUARD_V2' ? '触发幅度随近期波动调整；连续下跌、回撤、投入额度与冷却期共同限制加仓。' : '不足 1% 不操作；下跌分批买入，上涨分批卖出。'}</p>
          <div className="investment-plan-tiers">{[['1', 0.05], ['2', 0.10], ['3', 0.15]].map(([trigger, fraction]) => <div key={trigger}><span>达到 ±{trigger}{template === 'DRAWDOWN_GUARD_V2' ? 'h' : '%'}</span><strong>{validCap ? `¥${cash(Number(holdingCap) * Number(fraction))}` : '—'}</strong><span>当日累计原目标 · {Number(fraction) * 100}%</span></div>)}</div>
          {template === 'DRAWDOWN_GUARD_V2' && <p>h 按最近已披露净值的波动计算。防御状态买入目标减半，严重下行暂停加仓；先满足恢复条件，再重新计算金额。</p>}
        </div>
        <p className="profile-note">{template === 'DRAWDOWN_GUARD_V2' ? '每日原目标还会扣减已提交金额，并受近 5 个交易日投入上限、冷却期及成本占用额度约束。价格下跌不会自动释放已投入本金。' : '同向已提交金额会扣减；买卖合计每日最多为持仓上限的 15%，同日反向操作暂停参考。持仓与待买达到上限后停止建议加仓；报价上涨超过上限时不会要求强卖。'}</p>
        <p className="profile-note">卖出仅使用可用 T 仓，按实际 FIFO 核对免赎回费部分，并要求预计净实现收益至少为 2 元且不低于卖出额的 0.5%。这套纪律尚未证明盈利优势，场外基金按渠道确认净值成交。</p>
        {!enabled && <p className="inline-note">停用后恢复原有做 T 档位；持仓、底仓及历史记录保持。</p>}
        {error && <div className="error-banner" role="alert">{error}</div>}
      </div>
      <div className="modal-footer"><button className="button" type="button" onClick={onClose} disabled={busy}>取消</button><button className="primary" type="submit" disabled={busy || !validCap}>{busy ? '保存中…' : '保存计划'}</button></div>
    </form>
  </div></div>;
}
