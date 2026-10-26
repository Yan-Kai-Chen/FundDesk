import { useEffect, useState } from 'react';
import { api, cash, quantity, shanghaiInput, submittedTime, dateLabel } from './api';
import type { Row } from './api';
import './reconciliation.css';

const labels:Row={UNRECONCILED:'尚未核对',MATCHED:'份额已核对',DIFFERENCE:'存在差异',NOT_COMPARABLE:'仅保存资料',STALE:'需新截图核对'};
const signed=(v:unknown,units=false)=>v==null?'—':`${Number(v)>0?'+':''}${units?quantity(v):cash(v)}`;

export function ReconciliationResult({result,onOpenOrders}:{result:Row,onOpenOrders?:(fundId:string)=>void}){
  const differences=(result.comparisons||[]).filter((r:Row)=>r.status==='DIFFERENCE');
  return <div className="reconciliation-result" role="status">
    <div className="reconciliation-result-heading"><strong>{labels[result.status]||'持仓核对'}</strong>{result.input?.snapshot_at&&<span>{dateLabel(result.input.snapshot_at,'Asia/Shanghai',true)} · {result.input.source}</span>}</div>
    {result.stale&&<p className="reconciliation-notice">{result.stale_notice}</p>}
    {differences.length>0&&<div className="reconciliation-differences">{differences.map((r:Row)=><div key={r.field}><strong>{r.label}</strong><span>平台 {r.field==='confirmed_units'?quantity(r.reported):cash(r.reported)}{r.field==='confirmed_units'?' 份':' 元'}</span><span>本地 {r.field==='confirmed_units'?quantity(r.local):cash(r.local)}</span><strong>差 {signed(r.difference,r.field==='confirmed_units')}</strong></div>)}</div>}
    {result.status==='MATCHED'&&<p>已确认份额一致，自动入仓照常进行。</p>}
    {result.baseline?.pending_order_count>0&&<p>另有 {result.baseline.pending_order_count} 笔待确认申请，未计入已确认份额。</p>}
    <details><summary>核对依据{result.stale?' · 保存时':''}</summary><div className="reconciliation-basis">
      <p>截图时间：{result.input?.snapshot_at?dateLabel(result.input.snapshot_at,'Asia/Shanghai',true):'—'}（北京时间）。基准在保存时固定。</p>
      {(result.comparisons||[]).map((r:Row)=><p key={r.field}>{r.label}：平台 {r.reported}，本地 {r.local}，差 {signed(r.difference,r.field==='confirmed_units')}；容差 {r.tolerance}。</p>)}
      {(result.unassessed||[]).map((r:Row)=><p key={r.field}>{r.reason}</p>)}
      {result.baseline?.nav_date&&<p>正式净值日 {result.baseline.nav_date} · 净值 {quantity(result.baseline.nav_value,4)}</p>}
      <p>此记录只用于核对，不补差或生成交易。每只基金保留最近 5 次。</p>
    </div></details>
    {differences.length>0&&onOpenOrders&&<button className="button" onClick={()=>onOpenOrders(result.fund_id)}>查看原交易</button>}
  </div>;
}

type Props={fund:Row,mode:string,onSaved?:()=>Promise<void>,onOpenOrders?:(fundId:string)=>void};
export function ReconciliationPanel({fund,mode,onSaved,onOpenOrders}:Props){
  const [editing,setEditing]=useState(false),[preview,setPreview]=useState<Row|null>(null),[saved,setSaved]=useState<Row|null>(null);
  const [source,setSource]=useState('支付宝'),[snapshotAt,setSnapshotAt]=useState(()=>shanghaiInput(new Date().toISOString()));
  const [scope,setScope]=useState('CURRENT'),[units,setUnits]=useState(''),[value,setValue]=useState(''),[pnl,setPnl]=useState(''),[fee,setFee]=useState('');
  const [navDate,setNavDate]=useState(''),[valueBasis,setValueBasis]=useState('UNSPECIFIED'),[pnlBasis,setPnlBasis]=useState(false);
  const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
  useEffect(()=>{setEditing(false);setPreview(null);setSaved(null);setSource('支付宝');setUnits('');setValue('');setPnl('');setFee('');setNavDate('');setScope('CURRENT');setValueBasis('UNSPECIFIED');setPnlBasis(false);setMessage('');setSnapshotAt(shanghaiInput(new Date().toISOString()));},[fund.id,mode]);
  const incoming=fund.reconciliation?.latest;
  const latest=!saved?incoming:incoming&&incoming.saved_sequence>=saved.saved_sequence?incoming:saved;
  const invalidate=()=>{setPreview(null);setMessage('');};
  const calculate=async()=>{
    setBusy(true);setMessage('');
    try{setPreview(await api(`/funds/${fund.id}/reconciliation/preview`,mode,{source,snapshot_at:submittedTime(snapshotAt),scope,
      units_basis:'CONFIRMED_EXCLUDES_PENDING',confirmed_units:units,market_value:value||null,holding_pnl:pnl||null,
      reported_fee:fee||null,nav_date:navDate||null,valuation_basis:valueBasis,holding_pnl_basis:pnlBasis?'REMAINING_COST':'UNSPECIFIED'}));}
    catch(e){setMessage((e as Error).message);}finally{setBusy(false);}
  };
  const save=async()=>{
    if(!preview)return;setBusy(true);setMessage('');
    try{const result=await api(`/funds/${fund.id}/reconciliation`,mode,{...preview.input,preview_token:preview.preview_token,save_snapshot:true});
      setSaved(result);setPreview(null);setEditing(false);await onSaved?.();setMessage('快照已保存，交易和持仓未改动。');}
    catch(e){setMessage((e as Error).message);}finally{setBusy(false);}
  };
  return <details className="panel reconciliation-panel"><summary><strong>持仓核对</strong>{latest?.status==='DIFFERENCE'&&<span className="pill khaki">{latest.difference_count} 项差异</span>}{latest?.status==='STALE'&&<span className="pill">需新截图核对</span>}</summary>
    <div className="section-body">
      {!editing&&<><p>有差异时再处理，自动入仓无需逐笔确认。</p>{latest&&<ReconciliationResult result={latest} onOpenOrders={onOpenOrders}/>}<button className="button" onClick={()=>{setEditing(true);setSnapshotAt(shanghaiInput(new Date().toISOString()));setMessage('');}}>填写平台持仓</button></>}
      {editing&&<form onSubmit={e=>{e.preventDefault();void calculate();}} onChange={invalidate} className="reconciliation-form">
        <div className="reconciliation-fields"><label className="field">平台已确认份额<input required type="number" min="0" step="any" value={units} onChange={e=>setUnits(e.target.value)}/></label>
          <label className="field">截图持仓时间（北京时间）<input required type="datetime-local" value={snapshotAt} onChange={e=>setSnapshotAt(e.target.value)}/></label>
          <label className="field">资料来源<input required maxLength={120} value={source} onChange={e=>setSource(e.target.value)}/></label>
          <label className="field">持仓时点<select value={scope} onChange={e=>setScope(e.target.value)}><option value="CURRENT">当前截图，不含待确认</option><option value="HISTORICAL">历史截图，仅保存资料</option></select></label></div>
        <details><summary>补充金额与收益（选填）</summary><div className="reconciliation-fields">
          <label className="field">持有金额（元）<input type="number" min="0" step="0.01" value={value} onChange={e=>setValue(e.target.value)}/></label>
          <label className="field">持有收益（元）<input type="number" step="0.01" value={pnl} onChange={e=>setPnl(e.target.value)}/></label>
          <label className="field">所用净值日<input type="date" value={navDate} onChange={e=>setNavDate(e.target.value)}/></label>
          <label className="field">估值口径<select value={valueBasis} onChange={e=>setValueBasis(e.target.value)}><option value="UNSPECIFIED">尚未核实</option><option value="OFFICIAL_NAV">正式净值</option><option value="ESTIMATE">估算，只保留资料</option></select></label>
          <label className="field">费用（元，仅保留资料）<input type="number" min="0" step="0.01" value={fee} onChange={e=>setFee(e.target.value)}/></label>
        </div><label className="check-row"><input type="checkbox" checked={pnlBasis} onChange={e=>setPnlBasis(e.target.checked)}/>此处持有收益为市值减剩余成本</label></details>
        <div className="reconciliation-actions"><button type="submit" className="button" disabled={busy}>核对预览</button><button type="button" className="text-button" disabled={busy} onClick={()=>{setEditing(false);setPreview(null);setMessage('');}}>取消</button></div>
      </form>}
      {editing&&preview&&<><ReconciliationResult result={preview} onOpenOrders={onOpenOrders}/><button className="primary" disabled={busy} onClick={()=>void save()}>保存本次快照</button></>}
      {message&&<p role="status">{message}</p>}
    </div>
  </details>;
}
