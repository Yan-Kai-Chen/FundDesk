import { useEffect, useState } from 'react';
import { Check, Settings2 } from 'lucide-react';
import { api, cash, quantity, dateLabel } from './api';
import type { Row } from './api';
import { currentBuckets, returnBasisLabel } from './returnValuation';

const sign=(v:unknown)=>v==null?'—':`${Number(v)>0?'+':''}${cash(v)}`;
const names:Row={BASE:'底仓',TACTICAL:'做 T'};
type Props={fund:Row,mode:string,tz:string,onSaved:()=>Promise<void>,onExplain?:()=>void};

export function PositionBuckets({fund,mode,tz,onSaved,onExplain}:Props){
  const base=(fund.tracking||fund.migration_opening)?fund.position_buckets:fund.statement?.bucket_analysis||fund.position_buckets;
  const [preview,setPreview]=useState<Row|null>(null),[draft,setDraft]=useState<Row[]>([]);
  const [editing,setEditing]=useState(false),[dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
  useEffect(()=>{setPreview(null);setDraft([]);setEditing(false);setDirty(false);setMessage('');},[fund.id,mode]);
  const a=preview||base;
  const displayed=preview||editing?a:currentBuckets(fund,base);
  const calculate=async(method?:string)=>{
    setBusy(true);setMessage('');
    try{const result=await api(`/funds/${fund.id}/bucket-analysis`,mode,method?{method}:{assignments:draft,source_basis:'用户设置历史买卖的底仓/T归属'});setPreview(result);setDraft(result.assignments);setDirty(false);}
    catch(e){setMessage((e as Error).message);}finally{setBusy(false);}
  };
  const edit=()=>{setDraft(a.assignments.map((r:Row)=>({...r})));setEditing(true);setDirty(false);setMessage('');};
  const update=(id:string,k:string,value:string)=>{setDraft(old=>old.map(r=>r.row_id!==id?r:{...r,[k]:value,...(k==='bucket'?{t_units:value==='TACTICAL'?(r.t_units||a.buys.find((b:Row)=>b.id===id)?.units):undefined}:{})}));setDirty(true);};
  const save=async()=>{
    if(dirty)return;setBusy(true);setMessage('');
    try{await api(`/funds/${fund.id}/bucket-classification`,mode,{assignments:a.assignments,source_basis:a.source_basis,
      statement_version:a.statement_version,rule_version:a.rule_version,expected_version:a.classification_version},'PUT');await onSaved();setPreview(null);setEditing(false);setMessage('分仓归属已保存，以后按买卖选择自动扣仓。');}
    catch(e){setMessage((e as Error).message);}finally{setBusy(false);}
  };
  if(!a)return null;
  if(!displayed?.groups)return <section className="panel t-attribution"><div className="panel-heading"><h2>底仓与做 T</h2></div><div className="section-body"><p>{displayed?.notice||a.notice}</p>{fund.statement&&<button className="button" disabled={busy} onClick={()=>void calculate('FROM_RECENT_T')}>重新生成分仓草稿</button>}{message&&<p className="error-text">{message}</p>}</div></section>;
  const baseGroup=displayed.groups.BASE,tGroup=displayed.groups.TACTICAL,isReference=a.status==='REFERENCE',isTracking=a.status==='TRACKING';
  const live=!preview&&!editing&&base===fund.position_buckets&&fund.return_valuation;
  const day=live?fund.day_valuation:null;
  const rows=isReference?[...a.buys.map((b:Row)=>({...b,side:'BUY'})),...a.sells.map((s:Row)=>({...s,side:'SELL'}))].sort((x:Row,y:Row)=>x.at.localeCompare(y.at)):[];
  return <section className="panel t-attribution position-buckets">
    <div className="panel-heading"><h2>收益</h2><span className={`pill ${live?.is_estimate?'sage':'khaki'}`}>{live?returnBasisLabel(fund):fund.migration_opening?'当前持仓起点':isTracking?'含历史还原':isReference?'净值还原':'已确认账本'}</span></div>
    {fund.migration_opening&&<p className="section-body">收益包含导入成本后的持仓及后续操作；导入前的已实现收益待补。</p>}
    <div className={`t-metrics ${live?'live-return-metrics':''}`}>
      {live&&<div><span>今日变动参考</span><strong>{day?.status==='READY'?sign(day.price_change):'—'}<em>元</em></strong><small>{day?.status==='READY'?'当前份额的今日价格变动':day?.status==='NOT_OPEN'?'没开盘':'等待当天报价'}</small></div>}
      <div><span>底仓盈亏</span><strong>{sign(baseGroup.pnl)}<em>元</em></strong><small>持有 {sign(baseGroup.unrealized)} · 已实现 {sign(baseGroup.realized)}</small></div>
      <div className="t-focus"><span>做 T 已实现</span><strong>{sign(tGroup.realized)}<em>元</em></strong><small>T 仓持有浮动 {sign(tGroup.unrealized)} 元</small></div>
      <div><span>累计收益</span><strong>{live&&onExplain?<button type="button" className="number-link" onClick={onExplain} aria-label="查看收益计算依据">{sign(displayed.total_pnl)}</button>:sign(displayed.total_pnl)}<em>元</em></strong><small>底仓 + 做 T 的全部盈亏</small></div>
    </div>
    <details className="trade-details"><summary>逐笔卖出 <span className="count">{a.sales.length}</span></summary>
    <div className="table-wrap"><table><thead><tr><th>卖出日期 / 归属</th><th>T 仓扣减</th><th>底仓扣减</th><th>扣出成本</th><th>卖出净额</th><th>本笔盈亏</th></tr></thead><tbody>{a.sales.map((s:Row)=><tr key={s.id}><td><strong>{isReference||s.reconstructed?s.at.slice(0,10):dateLabel(s.at,tz,true)} · {names[s.intent]}</strong><small>{s.reconstructed?'历史还原 · '+(s.pending?'到账待核对':'已纳入起点'):s.pending?'待确认 · 估算':isReference?'交易列表还原':'已确认结果'}</small></td><td className="numeric">{quantity(s.t_units)} 份</td><td className="numeric">{quantity(s.base_units)} 份{Number(s.fallback_units)>0&&<small>补扣本金 ¥{cash(s.fallback_principal)}，盈亏归 T</small>}</td><td className="numeric">{cash(s.cost)}</td><td className="numeric">{cash(s.net)}<small>已扣费用 ¥{cash(s.fee)}</small></td><td className={`numeric ${Number(s.pnl)>=0?'up':'down'}`}><strong>{sign(s.pnl)}</strong></td></tr>)}</tbody></table>{!a.sales.length&&<div className="empty-inline">确认卖出后，在这里逐笔查看底仓和 T 盈亏。</div>}</div>
    </details>
    {isReference&&<div className="t-actions"><button className="text-button" disabled={busy} onClick={edit}><Settings2 size={14}/>历史买卖归属</button>{(!a.classification_saved||editing||preview)&&<button className="button" disabled={busy||dirty} onClick={()=>void save()}><Check size={14}/>保存分仓归属</button>}<span>初始拆分沿用已认可的复盘，后续新买卖直接选择底仓或做 T。</span></div>}
    {editing&&<div className="t-editor"><h3>历史记录一次分类</h3><p>每笔选择底仓或做 T。沿用旧复盘时，一笔买入可保留部分 T 份额，其余属于底仓。</p><div className="bucket-editor-list">{rows.map((r:Row)=>{const setting=draft.find(d=>d.row_id===r.id);return <div className="bucket-edit-row" key={r.id}><div><strong>{r.at.slice(0,16)} · {r.side==='BUY'?'买入':'卖出'}</strong><small>{r.side==='BUY'?`¥${cash(r.cost)} · `:''}{quantity(r.units)} 份</small></div><select aria-label={`${r.at}归属`} value={setting?.bucket||'BASE'} onChange={e=>update(r.id,'bucket',e.target.value)}><option value="BASE">底仓</option><option value="TACTICAL">做 T</option></select>{r.side==='BUY'&&setting?.bucket==='TACTICAL'?<label className="field">其中 T 份额<input aria-label={`${r.at}T份额`} type="number" min="0.01" step="0.01" value={setting.t_units??r.units} onChange={e=>update(r.id,'t_units',e.target.value)}/></label>:<span className="muted">{r.side==='SELL'&&setting?.bucket==='TACTICAL'?'不足部分补扣底仓，盈亏归T':'全部归入所选仓位'}</span>}</div>})}</div><button className="primary" disabled={busy} onClick={()=>void calculate()}>预览分仓收益</button>{dirty&&<p className="muted">草稿已修改，请先预览，再保存。</p>}</div>}
    {message&&<p className="t-message" role="status">{message}</p>}
    <details className="t-explanation"><summary>持仓与计算明细</summary>    <div className="bucket-holdings">{[['BASE',baseGroup],['TACTICAL',tGroup]].map(([id,g]:any)=><div key={id}><span>{id==='BASE'?'底仓':'T 仓'}</span><strong>{quantity(g.units)} <small>份</small></strong><span>市值 ¥{cash(g.value)} · 成本 ¥{cash(g.cost)} · 冻结 {quantity(g.reserved)} 份</span></div>)}</div>
    <div className="t-equation">底仓盈亏 <strong>{sign(baseGroup.pnl)}</strong><span>＋</span>做 T 盈亏 <strong>{sign(tGroup.pnl)}</strong><span>＝</span>累计收益 <strong>{sign(displayed.total_pnl)}</strong></div>
    <div className="t-equation">做 T 已实现 <strong>{sign(tGroup.realized)}</strong><span>＋</span>持有浮动 <strong>{sign(tGroup.unrealized)}</strong><span>＝</span>做 T 盈亏 <strong>{sign(tGroup.pnl)}</strong></div>
    <div className="t-basis">{live&&<><span>{returnBasisLabel(fund)}（北京时间）。持有盈亏随有效报价更新，已实现收益以确认卖出为准。</span><span>最近正式累计收益：{sign(live.official_total_pnl)} 元。今日变动参考按当前份额计算，未调整当日资金流；累计收益已按所示净值重估，不再叠加该变动。</span></>}<span>做 T 卖出先扣 T 仓，不足补扣底仓；整笔盈亏都归 T。</span><span>卖出净额减去全部扣出成本；赎回费用按原渠道批次计算。</span>{isReference&&<span>{a.classification_saved?'历史分仓已保存':'沿用当前复盘的分仓草稿，尚未保存'} · 未确认交易仍含估算</span>}{a.legacy_count>0&&<span>历史未标记记录暂按底仓归类，可通过审计更正原记录。</span>}</div>
{isTracking&&<p>按 {fund.tracking.nav_date} 净值从现有持仓划出底仓 ¥{cash(fund.tracking.base_value)}，其余归 T。以后按份额持有，市值随净值变化；历史已实现 T 收益保留。</p>}<p>买入按选择增加底仓或 T 仓；每仓按买入先后自动扣减成本。做 T 卖出先扣 T 仓，不足部分扣底仓份额和成本，整笔净额减全部成本均计入 T 盈亏。底仓卖出只扣底仓，两仓合计不足或份额已冻结时拒绝重复占用。</p><p>分仓用于收益归因；实际渠道的 FIFO、持有期和手续费保持原口径。做 T 累计盈亏包含已实现 T 盈亏与尚持有 T 仓的浮动盈亏，不把卖出取回的本金算成盈利。</p>{isReference&&<p>{a.assumptions?.join('；')}。正式持仓与现金仍待渠道确认资料对账。</p>}</details>
  </section>;
}
