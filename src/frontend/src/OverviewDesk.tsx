import { ArrowRight, RefreshCw } from 'lucide-react';
import type { Row } from './api';
import { cash, pct, orderTimeLabel, dateLabel } from './api';
import { currentDayDisplay } from './viewData';
import { fundSections } from './sidebarCatalog';
import { signedCash } from './SellImpact';
import { dayCoverageLabel, pendingOrders, needsOrderInput } from './overviewData';
import { currentBuckets, returnBasisLabel } from './returnValuation';
import './overview.css';
import { DailyAgenda } from './DailyAgenda';
import { PortfolioRisk } from './PortfolioRisk';

export function OverviewDesk({state, refreshing, onRefresh, onSelect, onOrder, onAgendaOrder, onRecords, onEdit,onExplainFund}: {
  state: Row; refreshing: boolean; onRefresh: () => void; onSelect: (id: string) => void;
  onOrder: (id: string, side: string) => void; onAgendaOrder:(target:Row)=>void; onRecords: () => void; onEdit: (order: Row) => void;
  onExplainFund?:(id:string)=>void;
}) {
  const visible = state.funds.filter((f: Row) => f.watching || Number(f.units) > 0 || f.initialization_pending).map((f: Row) => ({...f, watching:true}));
  const groups = fundSections(visible, state.fund_sidebar).filter(g => g.funds.length);
  const pending = pendingOrders(state.orders || []);
  return <div className="fund-main overview-desk">
    <DailyAgenda value={state.daily_agenda} onSelectFund={onSelect}
      onOrder={onAgendaOrder} onEditOrder={id=>{const order=(state.orders||[]).find((o:Row)=>o.id===id);if(order)onEdit(order);}} onRecords={onRecords}/>
    <PortfolioRisk value={state.portfolio_risk} onSelectFund={onSelect}/>
    <section className="panel" aria-label="全部基金总览">
      <div className="panel-heading"><h2>全部基金 <span className="count">{visible.length}</span></h2><div className="button-group"><span>{state.business_date?.replaceAll('-', '/')} · 北京时间</span>{state.mode==='real'&&<button className="button" onClick={onRefresh} disabled={refreshing}><RefreshCw size={16} className={refreshing?'refresh-spinning':''}/>{refreshing?'正在刷新…':'刷新全部'}</button>}</div></div>
      <div className="table-wrap"><table className="overview-table"><thead><tr><th>基金</th><th>今日涨跌</th><th>持仓市值</th><th>今日变动参考</th><th>累计收益</th><th className="align-right">记录交易</th></tr></thead><tbody>
        {groups.map(group => <OverviewGroup key={group.id} group={group} onSelect={onSelect} onOrder={onOrder} onExplain={onExplainFund}/>) }
      </tbody></table></div>
      {!visible.length&&<div className="empty-inline">还没有基金，请从左侧添加。</div>}
      <details className="overview-basis"><summary>数值口径</summary><p>市值、持有盈亏与累计收益优先采用当天正式净值，否则使用有效的实时、午间或收盘估算；当天报价不可用时沿用最近正式净值，并显示日期。</p><p>今日变动参考 = 当前已确认份额 ×（当天参考净值 − 上一交易日正式净值）。它表示当前持仓的价格变化，未调整当天资金流。累计收益已按显示净值重估，不再叠加该变动；估算不会写入正式账本。</p></details>
    </section>
    {!state.daily_agenda&&<section className="panel" aria-label="待处理交易">
      <div className="panel-heading"><h2>交易进度 <span className="count">{pending.length}</span></h2><button className="text-button" onClick={onRecords}>全部记录 <ArrowRight size={15}/></button></div>
      {!pending.length?<div className="overview-idle">当前没有等待处理的交易</div>:pending.slice(0,4).map((o:Row)=><div className="overview-progress" key={o.id}><div><strong>{state.funds.find((f:Row)=>f.id===o.fund_id)?.name||'基金'} · {o.side==='BUY'?'买入':'卖出'}</strong><small>{orderTimeLabel(o)} · 北京时间</small></div><div><span className={`pill ${needsOrderInput(o)?'khaki':'sage'}`}>{needsOrderInput(o)?'需补充':o.cancel_requested?'撤单申请中':o.automation?.automatic?'自动处理中':o.automation?.label||'等待处理'}</span><small>{o.automation?.detail||'等待确认'}</small></div><button className="text-button" onClick={()=>onEdit(o)}>查看 / 修改 <ArrowRight size={14}/></button></div>)}
    </section>}
  </div>;
}

function OverviewGroup({group, onSelect, onOrder,onExplain}: {group: Row; onSelect:(id:string)=>void; onOrder:(id:string,side:string)=>void;onExplain?:(id:string)=>void}) {
  return <><tr className="overview-group"><th colSpan={6}>{group.name}</th></tr>{group.funds.map((f:Row)=>{
    const day=currentDayDisplay(f), buckets=currentBuckets(f), values=buckets?.groups, impact=f.day_valuation;
    const holding=f.return_valuation?f.return_valuation.holding_value:f.market_value;
    return <tr key={f.id}>
      <td><button className="overview-fund" onClick={()=>onSelect(f.id)}><strong>{f.sidebar.display_name}</strong><small>{f.fund_code||'DEMO'}{f.sidebar.theme?` · ${f.sidebar.theme}`:''}</small></button></td>
      <td className="numeric"><strong>{day.ready?`${day.official?'':'≈ '}${pct(day.change)}`:day.label}</strong>{day.ready&&<small>{day.label}{day.asOf&&` · ${dateLabel(day.asOf,'Asia/Shanghai',true).split(' ').at(-1)}`}</small>}</td>
      <td className="numeric"><strong>¥{cash(holding)}</strong><small>底仓 {cash(values?.BASE?.value)}<br/>T 仓 {cash(values?.TACTICAL?.value)}</small></td>
      <td className="numeric"><strong>{impact?.status==='READY'?`${impact.is_estimate?'≈ ':''}${signedCash(impact.price_change)}`:'—'}</strong><small>{impact?.status==='READY'?'当前持仓价格变动':impact?.status==='NOT_OPEN'?'没开盘':'待更新'}</small></td>
      <td className="numeric"><strong>{onExplain?<button type="button" className="number-link" aria-label={`查看${f.sidebar.display_name}收益依据`} onClick={()=>onExplain(f.id)}>{signedCash(buckets?.total_pnl)}</button>:signedCash(buckets?.total_pnl)}</strong><small>{returnBasisLabel(f)}</small></td>
      <td className="align-right"><div className="overview-trade-actions"><button className="small-primary" aria-label={`买入 ${f.sidebar.display_name}`} onClick={()=>onOrder(f.id,'BUY')}>买入</button><button className="button sell-button" aria-label={`卖出 ${f.sidebar.display_name}`} onClick={()=>onOrder(f.id,'SELL')}>卖出</button></div></td>
    </tr>;
  })}</>;
}

export function DayValuationSummary({day}:{day?:Row}) {
  return <div className="summary-card" title={day?.detail}><div className="metric-label">今日变动参考</div><div className="metric-value">{day?.total!=null?<><span>¥</span>{signedCash(day.total)}</>:day?.known_subtotal!=null?<><span>已知 ¥</span>{signedCash(day.known_subtotal)}</>: '—'}</div><div className="metric-foot">{dayCoverageLabel(day)}</div></div>;
}
