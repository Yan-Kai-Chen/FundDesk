import { useEffect, useState } from 'react';
import { ArrowRight, ChevronRight } from 'lucide-react';
import { cash, dateLabel, quantity } from './api';
import type { Row } from './api';
import './dailyAgenda.css';

export type DailyAgendaProps = {
  value?: Row;
  onSelectFund: (fundId: string) => void;
  onOrder: (target: Row) => void;
  onEditOrder?: (orderId: string) => void;
  onRecords?: () => void;
};

const signed = (value: unknown) => value == null ? '—' : `${Number(value) > 0 ? '+' : ''}${cash(value)}`;

export function DailyAgenda({value, onSelectFund, onOrder, onEditOrder, onRecords}: DailyAgendaProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const clock = Date.now();
    const future = (value?.actions || []).map((row: Row) => Date.parse(row.expires_at || '')).filter((at: number) => at > clock);
    if (!future.length) return;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(1, Math.min(...future) - clock + 1));
    return () => window.clearTimeout(timer);
  }, [value, now]);
  return <DailyAgendaContent value={value} onSelectFund={onSelectFund} onOrder={onOrder} onEditOrder={onEditOrder} onRecords={onRecords} now={Math.max(now, Date.now())}/>;
}

// Export the pure view for rendering/interaction checks without a browser. The
// wrapper's single timer only expires buttons; it never refreshes or saves.
export function DailyAgendaContent({value, onSelectFund, onOrder, onEditOrder, onRecords, now = Date.now()}: DailyAgendaProps & {now?: number}) {
  if (!value) return null;
  const valid = (row: Row, clock: number) => !row.expires_at || Date.parse(row.expires_at) > clock;
  const actions: Row[] = (value.actions || []).filter((row: Row) => row.actionable && valid(row, now));
  const input: Row[] = value.needs_input || [], waiting: Row[] = value.waiting || [];
  const inactive: Row[] = (value.funds || []).filter((row: Row) => !row.actionable || !valid(row, now)).map((row: Row) => row.actionable ?
    {...row, actionable: false, label: '参考已过期', reason: '报价或申请时段已过期，请刷新后查看'} : row);
  const record = (row: Row) => onEditOrder ? onEditOrder(row.order_id) : onRecords?.();
  const orderRow = (row: Row) => <div className="agenda-order" key={row.id}>
    <div><button className="agenda-fund-name" onClick={() => onSelectFund(row.fund_id)}>{row.fund_name}</button><span>{row.side === 'SELL' ? `卖出 ${quantity(row.requested)} 份` : `买入 ¥${cash(row.requested)}`}</span></div>
    <div><strong>{row.label}</strong><span>{row.reason}</span></div>
    {(onEditOrder || onRecords) && <button className="text-button" onClick={() => record(row)}>{row.needs_input ? '补充记录' : '查看'}<ArrowRight size={16}/></button>}
  </div>;
  const actionRow = (row: Row) => <div className="agenda-action" key={row.id}>
    <div className="agenda-action-heading"><button className="agenda-fund-name" onClick={() => onSelectFund(row.fund_id)}>{row.fund_name}</button><span className="agenda-label">{row.action === 'SELL' ? '参考卖出' : '参考买入'} ¥{cash(row.amount)}</span></div>
    <div className="agenda-action-metrics">
      {row.action === 'SELL' ? <><span>{quantity(row.units)} 份</span><span>预计净实现收益 <strong>{signed(row.expected_realized_pnl)} 元</strong></span><span>预计到账 ¥{cash(row.net)}</span></> : <span>买入后尚无已实现收益</span>}
      <span>预计费用 {row.fee == null ? '待核对' : `¥${cash(row.fee)}`}</span>
    </div>
    <details className="agenda-reason"><summary>为什么这样操作</summary><p>{row.reason}</p>{row.basis && <p>{row.basis.value_kind === 'OFFICIAL_NAV' ? '正式净值' : '估算参考'} · {row.basis.effective_date}{row.basis.as_of ? ` · ${dateLabel(row.basis.as_of, 'Asia/Shanghai', true)} 北京时间` : ''}</p>}</details>
    {row.actionable && row.prefill && <button className="small-primary agenda-record" onClick={() => {if (valid(row, Date.now())) onOrder(row.prefill);}}>预填交易记录<ArrowRight size={16}/></button>}
  </div>;
  return <section className="daily-agenda" aria-label="今日待办">
    <header className="agenda-header"><div><h2>今日待办</h2><span>{input.length ? `${input.length} 笔交易需补充` : actions.length ? `${actions.length} 只基金有操作参考` : '当前无需新增操作'}</span></div><span>{value.date?.slice(5).replace('-', '/')} · 北京时间</span></header>
    {input.length > 0 && <div className="agenda-input"><strong className="agenda-section-label">需补充 {input.length} 笔</strong>{input.slice(0, 2).map(orderRow)}{input.length > 2 && <details><summary>其余 {input.length - 2} 笔需补充</summary>{input.slice(2).map(orderRow)}</details>}</div>}
    {actions.length > 0 ? <div className="agenda-actions">{actions.slice(0, 3).map(actionRow)}{actions.length > 3 && <details className="agenda-more"><summary>其余 {actions.length - 3} 只操作参考</summary>{actions.slice(3).map(actionRow)}</details>}<p className="agenda-footnote">仅预填本地记录，请在渠道完成实际买卖后保存。</p></div> : <p className="agenda-idle">当前没有可执行的做 T 参考。</p>}
    <div className="agenda-folds">
      {waiting.length > 0 && <details className="agenda-waiting"><summary><span className="agenda-waiting-dot"/>正常等待 {waiting.length} 笔<span> · 无需重复确认</span></summary>{waiting.map(orderRow)}</details>}
      {inactive.length > 0 && <details className="agenda-inactive"><summary><ChevronRight size={16}/>其余 {inactive.length} 只为何暂不操作</summary>{inactive.map(row => <div className="agenda-fund-reason" key={row.id}><button className="agenda-fund-name" onClick={() => onSelectFund(row.fund_id)}>{row.fund_name}</button><span>{row.label}</span><p>{row.reason}</p></div>)}</details>}
    </div>
  </section>;
}
