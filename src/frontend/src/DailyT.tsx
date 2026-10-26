import { useEffect, useRef, useState } from 'react';
import { ChevronDown, X } from 'lucide-react';
import { api, cash, pct, quantity, dateLabel } from './api';
import type { Row } from './api';
import { SellImpact } from './SellImpact';
import { InvestmentPlanReference } from './InvestmentPlan';

function ProfileBasis({ context, option }: { context: Row; option: Row }) {
  const p = context.presets;
  return <details className="profile-basis"><summary>计算依据</summary><div>
    <p>取今日之前最近 {p.sample_count} 个有效净值涨跌样本（最多 60 个）。触发幅度取绝对涨跌的{option.quantile ? `第 ${option.quantile} 百分位` : '历史分位'}，向上取整至 0.1 个百分点，最低 0.5%。均衡与积极档同时应用费用缓冲下限。</p>
    <p>单次额度为当前持仓市值的 {quantity(Number(option.fraction) * 100, 0)}%，单日做 T 买卖合计最多使用两次额度，已确认交易会扣减。金额随持仓变化，不重置底仓。</p>
    <p>卖出按实际批次 FIFO 估费；当前档位允许的最高批次费率为 {quantity(Number(option.fee_cap) * 100, 2)}%。高于上限的批次会拦截。T 仓不足时可使用底仓，整笔盈亏仍归 T。</p>
    <p>这是历史波动规则参考，尚未做收益回测；金额不代表渠道余额或成交价。参考市场日历与基金申赎开放日分别核对，实际开放与成交以渠道为准。</p>
    <p>{p.first_date || '—'} 至 {p.last_date || '—'} · {p.method_version}</p>
  </div></details>;
}

export function DailyT({ fund, reference, onSettings, onObservation, onInvestmentPlan, onOrder }: {
  fund: Row; reference?: Row; onSettings: () => void; onObservation: () => void;
  onInvestmentPlan: () => void; onOrder: (target: Row) => void;
}) {
  const context = fund.daily_t;
  if (!context) return null;
  const { market, today, profile, presets } = context;
  const option = presets.options.find((p: Row) => p.level === profile.level);
  const quote = today.quote;
  const gold = quote?.quality === 'LOCAL_GOLD_REFERENCE' ? quote.provenance : null;
  const sync = fund.intraday_sync;
  return <section className="daily-t" aria-label="今日涨跌与做T参考">
    <div className="daily-change"><div className="daily-label">{today.display_title||'今日涨跌'} <span>{market.date.slice(5).replace('-', '/')}</span></div>
      <strong className="daily-number">{today.change == null ? today.label : pct(today.change)}</strong>
      <div className="daily-caption">{quote ? <>{today.label} · {gold?.fixed_for_date ? '首次读取 ' : ''}{dateLabel(quote.as_of || quote.observed_at, 'Asia/Shanghai', true)} 北京时间</> : <>{market.detail}{market.status === 'OPEN' && ' · 等待当天报价'}</>}</div>
      {quote && <div className="daily-caption">{quote.value_kind === 'OFFICIAL_NAV' ? '正式净值' : '估算净值'} {quantity(quote.value,4)}</div>}
      {today.phase==='CLOSE'&&<div className="daily-caption">正式净值披露后自动替换</div>}
      {today.phase==='BREAK'&&<div className="daily-caption">保留上午最后报价，下午开盘后继续更新</div>}
      {sync?.status === 'FAILED' && today.status!=='READY' && today.status!=='NOT_OPEN' && sync.attempted_at?.slice(0,10)===market.date && <div className="daily-caption">本轮获取失败，保留上次数据，等待重连</div>}
      <div className="daily-caption">{quote?.source || (today.status==='NOT_OPEN'&&fund.automatic_market_supported?'开盘后自动获取':today.display_title==='收盘涨跌'?'等待收盘数据':sync ? '自动检查当天报价' : fund.automatic_market_supported ? '开盘后自动获取' : '暂无自动来源')}{quote?.provenance && today.phase==='LIVE' && ' · 每 10 分钟自动采集'}</div>
      {gold && <details className="daily-source"><summary>金价估算依据</summary><div className="daily-caption">
        {gold.fixed_for_date ? `${gold.fixing_date} 上海金午盘基准已公布，当日基准固定；来源未提供具体公布时刻。` : gold.reference_mode==='DAY_CLOSE_PROXY' ? '使用上金所 Au99.99 当日日间收盘价逼近，等待上海金午盘基准或正式净值。' : '午盘基准公布前使用上金所 Au99.99 延时报价逼近。'}<br/>
        当前金价 ¥{quantity(gold.gold_price,2)}/克 · 基准 {gold.previous_pm_date} ¥{quantity(gold.previous_pm_price,2)}/克<br/>
        上一正式净值 × [1 + 95% × (当前金价 ÷ 基准金价 − 1)]。95% 为业绩基准权重，并非当前实际仓位。<br/>
        {gold.historical_deviation?.mean_absolute_deviation != null && <>此前 {gold.historical_deviation.sample_count} 个样本的平均绝对跟踪偏差 {pct(gold.historical_deviation.mean_absolute_deviation)}；不代表预测范围。<br/></>}
        未计现金利息、当日费用及跟踪差异；正式净值披露后以正式值为准，估算不改写累计收益。
      </div></details>}
      {sync && <details className="daily-source"><summary>采集状态</summary><div className="daily-caption">最近尝试 {dateLabel(sync.attempted_at,'Asia/Shanghai',true)} 北京时间<br/>最近成功 {sync.last_success_at?dateLabel(sync.last_success_at,'Asia/Shanghai',true):'—'}<br/>{sync.error_message || '报价与采集时点分别保存；原始响应按期限轮换，关键依据保留。'}</div></details>}
    </div>
    {fund.investment_plan_profile?.enabled ? <InvestmentPlanReference fund={fund} reference={reference} onSettings={onInvestmentPlan} onOrder={onOrder}/> : <div className="daily-reference"><div className="daily-label"><span>做 T 参考</span><div className="button-group"><button className="text-button" onClick={onInvestmentPlan}>设定计划</button><button className={`intensity-button ${profile.level === 'OBSERVE' ? 'khaki' : 'sage'}`} aria-label="设置做T强度" onClick={onSettings}>{option.label}<ChevronDown size={16}/></button></div></div>
      <strong className="daily-number">{reference?.amount != null && reference.action !== 'HOLD' ? `${reference.label} ¥${cash(reference.amount)}` : reference?.label || '正在计算'}</strong>
      <div className="daily-caption">{reference?.units != null ? `${quantity(reference.units)} 份 · 预计费用 ¥${cash(reference.fee)}` : reference?.reason || '更新状态后查看'}</div>
      {reference?.action==='SELL'&&reference.sell_impact&&<SellImpact value={reference.sell_impact} compact/>}
      {profile.level !== 'OBSERVE' && option.trigger != null && <div className="daily-caption">下跌 {quantity(Number(option.trigger) * 100, 1)}% 买入 · 上涨 {quantity(Number(option.trigger) * 100, 1)}% 卖出 · 单次 ¥{cash(option.single_budget)}</div>}
      {market.status === 'OPEN' && today.change == null && <button className="text-button" onClick={onObservation}>录入今日报价</button>}
    </div>}
  </section>;
}

export function TProfileForm({ fund, mode, onClose, onSaved }: {
  fund: Row; mode: string; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const context = fund.daily_t;
  const [level, setLevel] = useState(context.profile.level);
  const [version] = useState(context.profile._version);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const modal = useRef<HTMLDivElement>(null);
  const option = context.presets.options.find((p: Row) => p.level === level);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    modal.current?.querySelector<HTMLInputElement>('input:checked')?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose();
      if (e.key === 'Tab') {
        const controls = Array.from(modal.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input') || []);
        const first = controls[0], last = controls.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); previous?.focus(); };
  }, [busy, onClose]);
  return <div className="modal-backdrop"><div className="modal wide" role="dialog" aria-modal="true" aria-labelledby="t-profile-title" ref={modal}>
    <div className="modal-header"><div><span className="eyebrow">{fund.name}</span><h2 id="t-profile-title">做 T 强度</h2></div><button className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={22}/></button></div>
    <form onSubmit={async e => { e.preventDefault(); setBusy(true); setError(''); try { await api(`/funds/${fund.id}/t-profile`, mode, { level, expected_version: version }, 'PUT'); await onSaved(); } catch (err) { setError((err as Error).message); } finally { setBusy(false); } }}>
      <div className="modal-body"><fieldset className="intensity-options"><legend>选择档位</legend>{context.presets.options.map((p: Row) => <label className={`intensity-option ${level === p.level ? 'chosen' : ''}`} key={p.level}>
        <input type="radio" name="t-intensity" value={p.level} checked={level === p.level} onChange={() => setLevel(p.level)} disabled={busy}/><strong>{p.label}</strong><span>{p.description}</span>
      </label>)}</fieldset>
      {level === 'OBSERVE' ? <div className="profile-preview">只显示当天涨跌，保留现有持仓和分仓，不生成买卖金额。</div> : <div className="profile-preview"><div><span>涨跌触发幅度</span><strong>{option.trigger == null ? '历史样本待补' : `±${quantity(Number(option.trigger) * 100, 1)}%`}</strong></div><div><span>单次参考上限</span><strong>¥{cash(option.single_budget)}</strong></div><div><span>单日参考上限</span><strong>¥{cash(option.daily_budget)}</strong></div></div>}
      {level !== 'OBSERVE' && !context.presets.ready && <p className="inline-note">历史样本不足或分红拆分口径待核对，当前暂停金额参考。</p>}
      <p className="profile-note">档位控制触发频率和参考额度。没有当天有效报价时只等待数据；选档不会下单或改写持仓。</p>
      {level !== 'OBSERVE' && <ProfileBasis context={context} option={option}/>}
      {error && <div className="error-banner" role="alert">{error}</div>}</div>
      <div className="modal-footer"><button type="button" className="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="primary" disabled={busy}>{busy ? '保存中…' : `使用${option.label}`}</button></div>
    </form>
  </div></div>;
}
