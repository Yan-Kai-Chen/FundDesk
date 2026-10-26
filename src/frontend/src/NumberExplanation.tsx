import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { api, cash, quantity, dateLabel, eventLabel } from './api';
import type { Row } from './api';
import { signedCash } from './SellImpact';
import './numberExplanation.css';

export function NumberExplanation({mode,fundId,onClose,onSelectFund}:{mode:string;fundId?:string;onClose:()=>void;onSelectFund?:(id:string)=>void}) {
  const [value,setValue]=useState<Row|null>(null),[error,setError]=useState('');
  const close=useRef(onClose);close.current=onClose;
  useEffect(()=>{
    const controller=new AbortController();setValue(null);setError('');
    void api(fundId?`/funds/${encodeURIComponent(fundId)}/valuation-explanation`:'/valuation-explanation',mode,undefined,undefined,controller.signal)
      .then(result=>{if(!controller.signal.aborted)setValue(result);}).catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    const escape=(e:KeyboardEvent)=>{if(e.key==='Escape')close.current();};
    window.addEventListener('keydown',escape);
    return()=>{controller.abort();window.removeEventListener('keydown',escape);};
  },[mode,fundId]);
  return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)onClose();}}><section className="number-explanation" role="dialog" aria-modal="true" aria-labelledby="number-explanation-title">
    <header><h2 id="number-explanation-title">{fundId?'收益与价格依据':'总盈亏依据'}</h2><button type="button" className="icon-button" onClick={onClose} aria-label="关闭计算依据" autoFocus><X size={22}/></button></header>
    <div className="number-explanation-body">{error?<p role="alert">{error}</p>:!value?<p>正在读取本机记录…</p>:<NumberExplanationContent value={value} onSelectFund={id=>{onSelectFund?.(id);onClose();}}/>}</div>
  </section></div>;
}

export function NumberExplanationContent({value:v,onSelectFund}:{value:Row;onSelectFund?:(id:string)=>void}) {
  if(v.scope==='PORTFOLIO')return <>
    <strong className="number-explanation-total">{signedCash(v.total_pnl)} <small>元</small></strong><p>{v.equation}</p>
    <div className="number-explanation-metrics"><div><span>基金合计</span><strong>{signedCash(v.fund_pnl)}</strong></div><div><span>其他跟踪项</span><strong>{signedCash(v.other_tracked_pnl)}</strong></div><div><span>本次估值差额</span><strong>{signedCash(v.mark_adjustment)}</strong></div></div>
    <div className="table-wrap"><table><thead><tr><th>基金</th><th>收益</th><th>价格依据</th></tr></thead><tbody>{v.funds.map((f:Row)=><tr key={f.fund_id}><td><button type="button" className="number-link" onClick={()=>onSelectFund?.(f.fund_id)}>{f.name}</button></td><td>{signedCash(f.components.pnl)}</td><td>{f.basis.label}<small>{f.basis.date||'待补'}</small></td></tr>)}</tbody></table></div>
    <p>{v.notice}</p><p>估值差额已包含在上方总盈亏中，不再相加。已知 {v.known_count} / {v.fund_count} 只。</p>
  </>;
  const c=v.components,b=v.basis,q=v.quote,errors=v.estimate_results;
  return <>
    <div className="number-explanation-subtitle"><strong>{v.name}</strong><span>{v.fund_code}</span></div>
    <strong className="number-explanation-total">{signedCash(c.pnl)} <small>元</small></strong><p>{v.equation}</p>
    <div className="number-explanation-metrics"><div><span>当前市值</span><strong>¥{cash(c.value)}</strong></div><div><span>剩余成本</span><strong>¥{cash(c.cost)}</strong></div><div><span>已实现收益</span><strong>{signedCash(c.realized)}</strong></div></div>
    <section><h3>价格</h3><p>{b.label} · {b.date||'待补价格'} · 净值 {quantity(q.value,4)}</p><p>{q.source||'来源待补'}{b.as_of&&` · ${dateLabel(b.as_of,'Asia/Shanghai',true)} 北京时间`}</p>
      <p>最近正式累计收益 {signedCash(c.official_pnl)} 元；本次估值差额 {signedCash(c.mark_adjustment)} 元。</p><p>{v.price_notice}</p></section>
    <section><h3>交易与费用</h3><p>持有盈亏 {signedCash(c.unrealized)} 元 · 已实现 {signedCash(c.realized)} 元。</p><p>记录中的卖出费用 ¥{cash(v.recorded_sale_fees)}。{v.fee_scope}</p><p>{v.fee_notice}</p></section>
    <details><summary>最近记录与更正{v.correction_count>0?` · ${v.correction_count} 次冲正`:''}</summary><p>以下为导致份额、成本或确认状态变化的记录；不是另算一笔收益。校正后以当前账本为准。</p>
      {(v.recent_events||[]).map((e:Row)=><div className="number-explanation-event" key={e.id}><span>{eventLabel[e.kind]||e.kind}{e.reversed?' · 已冲正':''}</span><span>{dateLabel(e.occurred_at,'Asia/Shanghai',true)}</span>{e.payload.reason&&<p>{e.payload.reason}</p>}</div>)}
      <p>{v.history_notice}</p></details>
    <details><summary>收盘估算与正式值偏差{errors?.resolved?` · ${errors.resolved} 次核对`:''}</summary><p>{errors?.notice}</p>
      {errors?.resolved>0&&<p>首次取得正式值的涨跌平均绝对误差 {errors.mae_pp==null?'—':quantity(errors.mae_pp,2)} 个百分点 · 可比 {errors.scored_count} 次。</p>}
      {errors?.rows?.length?<div className="table-wrap"><table><thead><tr><th>日期</th><th>收盘估算</th><th>首次正式</th><th>涨跌误差</th></tr></thead><tbody>{errors.rows.map((r:Row)=><tr key={r.date}><td>{r.date}</td><td>{quantity(r.estimate.value,4)}<small>{r.estimate.source}</small></td><td>{r.formal_first?quantity(r.formal_first.value,4):'等待披露'}{r.formal_latest&&r.formal_latest.id!==r.formal_first?.id&&<small>修订 {quantity(r.formal_latest.value,4)}</small>}</td><td>{r.first_comparison?.change_error_pp!=null?`${quantity(r.first_comparison.change_error_pp,2)} 个百分点`:r.status==='PENDING'?'等待':'基准待核对'}</td></tr>)}</tbody></table></div>:<p>尚无满足时间条件的收盘记录，从合格数据开始积累。</p>}
    </details>
  </>;
}
