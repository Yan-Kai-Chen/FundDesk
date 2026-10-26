import type { Row } from './api';
import { cash, quantity } from './api';
import { signedCash } from './SellImpact';
import { DayValuationSummary } from './OverviewDesk';
import { portfolioReturnLabel } from './returnValuation';
import { HoldingComposition } from './HoldingComposition';

export function TrackingSummary({portfolio:p,overview=false,onExplain}:{portfolio:Row;overview?:boolean;onExplain?:()=>void}){
  const display=p.return_valuation||p;
  return <section className={`summary-grid ${overview?'overview-summary':''}`} aria-label="总持仓与总盈亏">
    <div className="summary-card total"><div className="metric-label">总持仓</div><div className="metric-value"><span>¥</span>{cash(display.market_value)}</div><div className="metric-foot">{portfolioReturnLabel(p)}{Number(p.in_transit)>0&&<span>待确认 / 待到账 ¥{cash(p.in_transit)}</span>}</div></div>
    <div className="summary-card profit"><div className="metric-label">总盈亏</div><div className="metric-value"><span>¥</span>{onExplain?<button type="button" className="number-link" onClick={onExplain} aria-label="查看总盈亏计算依据">{signedCash(display.pnl)}</button>:signedCash(display.pnl)}</div><div className="metric-foot">底仓与做 T 全部盈亏 · {portfolioReturnLabel(p)}</div></div>
    {overview?<DayValuationSummary day={p.day_valuation}/>:<><div className="summary-card"><div className="metric-label">底仓合计</div><div className="metric-value"><span>¥</span>{cash(display.bucket_values?.BASE)}</div></div>
    <div className="summary-card"><div className="metric-label">T 仓合计</div><div className="metric-value"><span>¥</span>{cash(display.bucket_values?.TACTICAL)}</div></div></>}
  </section>;
}

export function FundHoldings({fund,onExplain}:{fund:Row;onExplain?:()=>void}){
  return <HoldingComposition fund={fund} onExplain={onExplain}/>;
}

export function TrackingHistory({fund,compact=false}:{fund:Row,compact?:boolean}){
  const rows=[...(fund.tracking?.historical_rows||[])].reverse();
  return <div className="table-wrap"><table><thead><tr><th>历史时间 / 方向</th><th>金额</th><th>还原份额</th><th>当日净值</th><th>记录依据</th></tr></thead><tbody>{(compact?rows.slice(0,3):rows).map((r:Row)=><tr key={r.id}><td><strong>{r.at.slice(0,10)} · {r.side==='BUY'?'买入':'卖出'}</strong><small>{r.at.slice(11)} · 北京时间</small></td><td className="numeric">¥{cash(r.side==='BUY'?r.cost:r.net)}<small>{r.side==='BUY'?'原买入金额':r.pending?'卖出净额估算':'原列表净额'}</small></td><td className="numeric">{quantity(r.units)} 份</td><td className="numeric">{quantity(r.nav,4)}</td><td><span className="pill">已纳入跟踪起点</span><small>{r.pending?'到账仍以实际回填为准':'截图和当日净值，非渠道确认单'}</small></td></tr>)}</tbody></table></div>;
}
