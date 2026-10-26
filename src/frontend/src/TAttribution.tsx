import { useEffect, useState } from 'react';
import { Check, ChevronRight, Plus, RefreshCw, Settings2, X } from 'lucide-react';
import type { Row } from './api';
import { api, cash, quantity } from './api';

const methods: Row = {RECENT_BUY_FIRST:'最近加仓优先 · 先买后卖',NEXT_BUY_FIRST:'卖后回补优先 · 先卖后买',MANUAL:'自定义买卖配对',NONE:'暂不配对'};
const signed = (v: unknown) => v == null ? '—' : `${Number(v)>0?'+':''}${cash(v)}`;

export function TAttribution({fund,mode,onSaved}:{fund:Row,mode:string,onSaved:()=>Promise<void>}) {
  const base = fund.statement?.t_analysis;
  const [preview,setPreview] = useState<Row|null>(null),[draft,setDraft] = useState<Row[]>([]);
  const [editor,setEditor] = useState(false),[dirty,setDirty] = useState(false),[busy,setBusy] = useState(false),[message,setMessage] = useState('');
  useEffect(()=>{setPreview(null);setDraft([]);setEditor(false);setDirty(false);setMessage('');},[fund.id]);
  const a = preview || base;
  const calculate = async(method:string,pairs:Row[]=draft)=>{
    setBusy(true);setMessage('');
    try { const result=await api(`/funds/${fund.id}/t-analysis`,mode,{method,pairs});setPreview(result);setDraft(result.pairs);setDirty(false); }
    catch(e) {setMessage((e as Error).message);} finally {setBusy(false);}
  };
  const edit = ()=>{setDraft((a?.pairs||[]).map((p:Row)=>({...p})));setEditor(true);setDirty(true);setMessage('');};
  const change = (i:number,k:string,value:string)=>{setDraft(old=>old.map((p,j)=>j===i?{...p,[k]:value}:p));setDirty(true);};
  const save = async()=>{
    if(dirty)return;
    setBusy(true);setMessage('');
    try {await api(`/funds/${fund.id}/t-pairings`,mode,{method:a.method,pairs:a.pairs,statement_version:a.statement_version,rule_version:a.rule_version,expected_version:a.pairing_version},'PUT');await onSaved();setPreview(null);setEditor(false);setMessage('配对已保存，总盈亏核对一致。');}
    catch(e){setMessage((e as Error).message);}finally{setBusy(false);}
  };
  if(!a)return null;
  if(a.status!=='REFERENCE')return <section className="panel t-attribution"><div className="panel-heading"><h2>做 T 复盘</h2></div><div className="section-body"><p>{a.notice}</p><button className="button" disabled={busy} onClick={()=>void calculate('RECENT_BUY_FIRST')}><RefreshCw size={14}/>重新核对配对</button>{message&&<p className="error-text">{message}</p>}</div></section>;
  return <section className="panel t-attribution">
    <div className="panel-heading"><div><h2>做 T 复盘 <span className="pill">净值还原 · 估算</span></h2><small>{fund.fund_code} · 净值日 {a.nav_date} · {quantity(a.nav,4)} 元 / 份</small></div><select aria-label="做T配对方式" value={editor?'MANUAL':a.method} disabled={busy} onChange={e=>{if(e.target.value==='MANUAL')edit();else{setEditor(false);void calculate(e.target.value);}}}>{Object.entries(methods).map(([k,v])=><option key={k} value={k}>{String(v)}</option>)}</select></div>
    <div className="t-metrics">
      <div><span>{a.residual_label}</span><strong className={Number(a.residual_pnl)>=0?'up':'down'}>{signed(a.residual_pnl)}<em>元</em></strong><small>{a.all_sales_paired?'留在底仓的成本与当前市值之差':'包含没有配对为T的仓位和交易'}</small></div>
      <div className="t-focus"><span>做 T 配对净差价</span><strong className={a.t_pnl==null?'':Number(a.t_pnl)>=0?'up':'down'}>{signed(a.t_pnl)}<em>元</em></strong><small>{a.paired_sales_count} / {a.sell_count} 笔卖出已完整配对{a.pending_sell_count?' · 含待确认估算':''}</small></div>
      <div><span>这只基金合计盈亏</span><strong className={Number(a.total_pnl)>=0?'up':'down'}>{signed(a.total_pnl)}<em>元</em></strong><small>配对只改变归因，总盈亏保持一致</small></div>
    </div>
    <div className="t-equation">{a.t_pnl==null?<>尚未配对做 T，合计盈亏 <strong>{signed(a.total_pnl)}</strong> 元暂归入其余盈亏。</>:<>{a.all_sales_paired?'底仓浮盈亏':'其余盈亏'} <strong>{signed(a.residual_pnl)}</strong><span>＋</span>做 T 净差价 <strong>{signed(a.t_pnl)}</strong><span>＝</span>合计 <strong>{signed(a.total_pnl)}</strong></>}</div>
    <div className="t-basis"><span>{a.pairing_saved?'已保存的买卖配对':'当前为假设配对，可展开核对或自定义'}</span><span>赎回费用已含在净额中，仍按原批次估算；年费已体现在净值内。</span></div>
    <div className="table-wrap"><table><thead><tr><th>卖出日期 / 状态</th><th>配对买入</th><th>配对份额</th><th>配对卖出净额</th><th>配对买入成本</th><th>本次 T 净差价</th></tr></thead><tbody>{a.sell_details.map((s:Row)=><tr key={s.id}><td><strong>{s.at.slice(0,10)}</strong><small>{s.pending?'卖出待确认 · 情景估算':s.remaining_units!=='0'&&Number(s.remaining_units)>0?'尚有份额待配对':'交易列表还原估算'}</small></td><td>{s.pairs.length?<details className="t-pair-detail"><summary>{s.pairs.length} 笔买入 <ChevronRight size={12}/></summary>{s.pairs.map((p:Row)=><div key={p.buy_id}><strong>{p.buy_at.slice(0,10)} · {quantity(p.units)} 份</strong><small>{p.direction==='BUY_THEN_SELL'?'先买后卖':'先卖后回补'} · 成本 ¥{cash(p.buy_cost)} · 卖出净额 ¥{cash(p.sell_net)}</small></div>)}</details>:<span className="muted">等待选定买入 / 回补</span>}</td><td className="numeric">{quantity(s.paired_units)}<small>共 {quantity(s.units)} 份</small></td><td className="numeric">{s.pairs.length?cash(s.paired_net):'—'}<small>原卖单费 ¥{cash(s.fee)}</small></td><td className="numeric">{s.pairs.length?cash(s.paired_cost):'—'}</td><td className={`numeric ${s.t_pnl==null?'':Number(s.t_pnl)>=0?'up':'down'}`}><strong>{signed(s.t_pnl)}</strong></td></tr>)}</tbody></table></div>
    <div className="t-actions"><button className="text-button" disabled={busy} onClick={edit}><Settings2 size={14}/>自定义配对</button><button className="button" disabled={busy||dirty} onClick={()=>void save()}><Check size={14}/>保存当前配对</button><span>保存分析配对后，原始交易与资产金额保持原有口径。</span></div>
    {editor&&<div className="t-editor"><h3>选择每次 T 对应的买入和卖出</h3><p>同一份额只配对一次。可以先买后卖，也可以先卖后回补；没有回补的份额保留待配对。</p>{draft.map((p,i)=><div className="t-edit-row" key={i}><select aria-label={`第${i+1}行买入`} value={p.buy_id} onChange={e=>change(i,'buy_id',e.target.value)}>{a.buys.map((b:Row)=><option key={b.id} value={b.id}>{b.at.slice(0,16)} 买入 ¥{cash(b.cost)} / {quantity(b.units)}份</option>)}</select><select aria-label={`第${i+1}行卖出`} value={p.sell_id} onChange={e=>change(i,'sell_id',e.target.value)}>{a.sells.map((s:Row)=><option key={s.id} value={s.id}>{s.at.slice(0,10)} 卖出 {quantity(s.units)}份</option>)}</select><input aria-label={`第${i+1}行配对份额`} value={p.units} type="number" min="0.01" step="0.01" onChange={e=>change(i,'units',e.target.value)}/><button className="icon-button" aria-label={`删除第${i+1}行配对`} onClick={()=>{setDraft(draft.filter((_,j)=>i!==j));setDirty(true);}}><X size={14}/></button></div>)}<div className="button-group"><button className="button" disabled={busy||!a.buys.length||!a.sells.length} onClick={()=>{setDraft([...draft,{buy_id:a.buys[a.buys.length-1]?.id,sell_id:a.sells[a.sells.length-1]?.id,units:'0.01'}]);setDirty(true);}}><Plus size={14}/>增加配对</button><button className="primary" disabled={busy} onClick={()=>void calculate('MANUAL',draft)}>预览配对收益</button></div>{dirty&&<p className="muted">配对草稿已更改，预览后更新上方收益。</p>}</div>}
    {message&&<div className="t-message" role="status">{message}</div>}
    <details className="t-explanation"><summary>查看收益口径与 FIFO 账本核对</summary><p>持仓市值是资产金额。持仓浮盈亏需要减去对应成本；累计盈亏包含已经卖出的部分。按 FIFO 还原：卖出部分盈亏 {signed(a.fifo_realized)} 元＋剩余持仓浮盈亏 {signed(a.fifo_unrealized)} 元＝合计 {signed(a.total_pnl)} 元。</p><p>上方 T 配对把选定的买入成本分配给对应卖出，剩余成本归入底仓。T 净差价不能再与 FIFO 浮盈亏相加。它是选定买卖的配对结果，不代表与另一套投资策略相比的超额收益。</p><p>{a.assumptions.join('；')}。含待确认卖出的数值仍为情景估算。</p></details>
  </section>;
}
