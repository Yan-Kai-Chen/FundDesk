import { useEffect, useState } from 'react';
import type { Row } from './api';
import { api, cash, quantity, dateLabel, pct } from './api';

export const signedCash = (v: unknown) => v == null ? '—' : `${Number(v)>0?'+':''}${cash(v)}`;

export function SellImpact({ value, compact=false, referenceSelection='', onReferenceChange, onEditBuy }: {
  value: Row; compact?: boolean; referenceSelection?: string;
  onReferenceChange?: (id: string) => void; onEditBuy?: (id: string) => void;
}) {
  const [basisOpen,setBasisOpen]=useState(false);
  const tBefore=value.before.groups.TACTICAL, tAfter=value.after.groups.TACTICAL;
  const comparison=value.buy_comparison;
  const knownComparison=comparison?.status==='READY';
  return <section className={`sell-impact ${compact?'compact':''}`} aria-label="卖出预期收益变化">
    {Number(value.fallback_units)>0&&<div className="sell-fallback" role="note"><strong>本次含底仓卖出</strong><span>{quantity(value.units)} 份 = T 仓 {quantity(value.t_units)} 份 + 底仓 {quantity(value.fallback_units)} 份</span></div>}
    <div className="sell-return-grid">
      {knownComparison&&<div className="sell-return-card price-comparison"><span>{comparison.bucket==='TACTICAL'?'本轮 T 价差参考':'买入价差参考 · 底仓'}</span><strong><button type="button" className="number-link" aria-label="查看价差参考依据" aria-expanded={basisOpen} onClick={()=>setBasisOpen(open=>!open)}>{signedCash(comparison.net_price_edge)}</button> <small>元</small></strong><small>{dateLabel(comparison.submitted_at,'Asia/Shanghai')}买入 · 覆盖 {quantity(comparison.covered_units)} 份</small></div>}
      <div className="sell-return-card"><span>按原成本实现损益</span><strong><button type="button" className="number-link" aria-label="查看卖出预期损益依据" aria-expanded={basisOpen} onClick={()=>setBasisOpen(open=>!open)}>{signedCash(value.expected_realized_pnl)}</button> <small>元</small></strong><small>到账 {cash(value.net)} − 扣出成本 {cash(value.removed_cost)}</small></div>
    </div>
    <p className="sell-impact-note">{value.basis?.effective_date||'—'} {value.basis?.value_kind==='OFFICIAL_NAV'?'正式净值':'估算净值'} {quantity(value.nav,4)}{value.day_change!=null&&<> · 较昨日 {pct(value.day_change)}</>}</p>
    {knownComparison&&<p className="sell-impact-note">价差仅比较所参照的买入成本，不计入累计收益。{Number(comparison.uncovered_units)>0&&<>另外 {quantity(comparison.uncovered_units)} 份未纳入价差参考。</>}{comparison.bucket==='BASE'&&<>这笔买入当前登记在底仓。</>}</p>}
    {!compact&&comparison&&onReferenceChange&&comparison.options?.length>0&&<label className="sell-reference-select">价差参照<select aria-label="价差参照买入" value={referenceSelection} onChange={e=>onReferenceChange(e.target.value)}><option value="">自动选择（优先做 T）</option>{comparison.options.map((option:Row)=><option value={option.order_id} key={option.order_id}>{dateLabel(option.submitted_at,'Asia/Shanghai')} · {option.bucket==='TACTICAL'?'做 T':'底仓'} · 剩余 {quantity(option.remaining_units)} 份</option>)}</select></label>}
    {comparison&&!knownComparison&&<p className="sell-impact-note">{comparison.notice}</p>}
    {comparison?.order_id&&comparison.bucket==='BASE'&&onEditBuy&&<button type="button" className="text-button sell-reference-edit" onClick={()=>onEditBuy(comparison.order_id)}>查看 / 修改这笔买入归属</button>}
    <div className="sell-impact-line"><span>预计到账</span><span>¥{cash(value.net)} · 手续费 ¥{cash(value.fee)}</span></div>
    <div className="sell-impact-line"><span>基金累计收益<span className="impact-unit">（同参考净值）</span></span><span>{signedCash(value.before.total_pnl)} → {signedCash(value.after.total_pnl)}</span></div>
    <p className="sell-impact-note">卖出对累计收益的影响 {signedCash(value.sell_effect)} 元，主要为手续费。原成本损益是已有浮盈亏转为已实现，不是再增减一遍。</p>
    {value.status!=='READY'&&<p className="sell-impact-note">成本资料不完整，收益未知；补全后才可预览做 T 卖出。</p>}
    <details className="sell-impact-basis" open={basisOpen} onToggle={e=>setBasisOpen(e.currentTarget.open)}><summary>损益拆解与依据</summary>
      <div className="sell-impact-line"><span>卖出金额 − 原成本</span><span>{signedCash(value.holding_pnl_before_fee)} 元</span></div>
      <div className="sell-impact-line"><span>减：卖出费用</span><span>¥{cash(value.fee)}</span></div>
      <div className="sell-impact-line"><span>{value.bucket==='TACTICAL'?'T 账本已实现收益':'底仓已实现收益'}</span><span>{signedCash(value.realized_before)} → {signedCash(value.realized_after)}</span></div>
      {value.bucket==='TACTICAL'&&<div className="sell-impact-line"><span>T 仓持有盈亏</span><span>{signedCash(tBefore.unrealized)} → {signedCash(tAfter.unrealized)}</span></div>}
      {knownComparison&&<p>参照买入剩余成本 ¥{cash(comparison.remaining_cost)}，剩余 {quantity(comparison.remaining_units)} 份；按本次参考价的持有浮盈亏 {signedCash(comparison.remaining_holding_pnl)} 元。<br/>本次覆盖 {quantity(comparison.covered_units)} 份的价差 {signedCash(comparison.gross_price_edge)} 元，减按份额分摊的本次卖出费 ¥{cash(comparison.fee_share)}，价差参考为 {signedCash(comparison.net_price_edge)} 元。买入费用已含在成本中，不重复扣除。<br/>{comparison.available_units!=null&&Number(comparison.available_units)<Number(comparison.remaining_units)&&<>参照买入按本机记录可赎回 {quantity(comparison.available_units)} 份；价差参照不代表这些新买份额已可卖，本次可卖量另按实际批次核对。<br/></>}价差不是渠道实际卖出批次的结算损益，也不会自动配对或改写仓位。</p>}
      {Number(value.fallback_units)>0&&<p>补扣底仓成本 ¥{cash(value.fallback_principal)}；整笔结算盈亏仍按原约定归 T。</p>}
      <p>
      参考净值 {quantity(value.nav,8)} · {value.basis?.effective_date||'—'} · {value.basis?.value_kind==='OFFICIAL_NAV'?'正式净值参考':'估算参考'}<br/>
      {value.basis?.source||'当前参考报价'}{value.basis?.as_of?` · ${dateLabel(value.basis.as_of,'Asia/Shanghai',true)} 北京时间`:''}<br/>
      扣出成本 ¥{cash(value.removed_cost)}，手续费按渠道实际批次 FIFO 与申请日 {value.fee_reference_date} 估算。<br/>
      当前正式累计收益 {signedCash(value.official_total_pnl)} 元；参考净值下不卖出为 {signedCash(value.before.total_pnl)} 元，卖出后为 {signedCash(value.after.total_pnl)} 元。<br/>
      {Number(value.rounding_effect)!==0&&<>金额分批取整影响 {signedCash(value.rounding_effect)} 元。<br/></>}
      估算不会下单或改账，实际净值日、费用和收益在渠道确认后回填。
    </p></details>
  </section>;
}

export function SellPreview({ fundId, mode, input, requestKey, onReady, onEditBuy }: {
  fundId: string; mode: string; input: Row; requestKey: string; onReady: (key: string) => void; onEditBuy?: (id: string) => void;
}) {
  const [result,setResult]=useState<{key:string;value?:Row;error?:string}>({key:''});
  const [refresh,setRefresh]=useState(0);
  const [reference,setReference]=useState('');
  const comparisonKey=JSON.stringify([requestKey,reference]);
  useEffect(()=>{
    let current=true;
    const controller=new AbortController();
    onReady('');
    if (!input.requested || Number(input.requested)<=0 || !input.occurred_at) return ()=>controller.abort();
    const timer=setTimeout(()=>{
      void api(`/funds/${fundId}/sell-preview`,mode,{...input,reference_buy_order_id:reference||null},undefined,controller.signal).then(value=>{
        if(current){setResult({key:comparisonKey,value});onReady(value.status==='READY'?requestKey:'');}
      }).catch(error=>{if(current&&!controller.signal.aborted)setResult({key:comparisonKey,error:(error as Error).message});});
    },350);
    return ()=>{current=false;clearTimeout(timer);controller.abort();};
  },[fundId,mode,comparisonKey,onReady,refresh]);
  const visible=result.key===comparisonKey?result:undefined;
  return <div className="sell-preview" aria-live="polite">
    {visible?.value?<SellImpact value={visible.value} referenceSelection={reference} onReferenceChange={id=>{onReady('');setReference(id);}} onEditBuy={onEditBuy}/>:<div className="sell-preview-placeholder"><strong>卖出预期收益变化</strong><p>{visible?.error||(!input.requested||Number(input.requested)<=0?'填写有效卖出份额后，自动显示本笔收益及卖出前后变化。':!input.occurred_at?'请填写有效的申请时间。':'正在计算预计收益…')}</p></div>}
    {input.requested&&Number(input.requested)>0&&input.occurred_at&&<button type="button" className="text-button" onClick={()=>{setResult({key:''});onReady('');setRefresh(v=>v+1);}}>重新估算</button>}
  </div>;
}
