import { useState } from 'react';
import type { Row } from './api';
import { api, cash, dateLabel, quantity } from './api';
import { signedCash } from './SellImpact';
import './tReview.css';

export function TReviewPanel({fund, mode, revision, onChanged}:{fund:Row;mode:string;revision?:number;onChanged?:()=>void|Promise<void>}) {
  const review = fund.t_review;
  const [busy,setBusy] = useState(false), [error,setError] = useState('');
  if(!review)return null;
  const exit = review.exit_reference, scenario = review.scenario, baseline = review.baseline;
  const ready = scenario?.status==='READY_SCENARIO';
  async function start(){
    setBusy(true);setError('');
    try{
      await api(`/funds/${encodeURIComponent(fund.id)}/t-review/baseline`,mode,{
        expected_revision:revision,expected_version:baseline?._version||0,
        policy:'VIRTUAL_RETAINED_PROCEEDS',accept_scenario:true,
      });
      await onChanged?.();
    }catch(e){setError(e instanceof Error?e.message:'无法建立比较基准');}finally{setBusy(false);}
  }
  return <details className="t-review-panel">
    <summary><span>做 T 复盘</span><span className="t-review-tag">{ready?'情景对照':exit?.count?`${exit.count} 次卖出参照`:'暂无做 T 卖出'}</span></summary>
    <div className="t-review-body">
      <section>
        <div className="t-review-heading"><h3>卖出后持有参照</h3><span>{exit?.count||0} 次</span></div>
        <strong className="t-review-amount">{signedCash(exit?.difference)} <small>元</small></strong>
        <p>{exit?.count?'逐次退出参照合计，不等同整个基金做 T 增益。每次为已卖净额 − 同批份额继续持有至今的市值。':'尚无已确认的做 T 卖出，退出参照为 0。'}</p>
        {!!exit?.pending_count&&<p>另有 {exit.pending_count} 笔资料待补，未计入上值。</p>}
        <details><summary>计算与来源</summary><p>{exit?.detail}</p>
          <p>{exit?.quote?.label} · {exit?.quote?.date||'等待净值'} · 净值 {quantity(exit?.quote?.value,4)}</p>
          {!!exit?.rows?.length&&<div className="table-wrap"><table><thead><tr><th>卖出日期</th><th>净额</th><th>继续持有市值</th><th>差额</th></tr></thead><tbody>
            {exit.rows.map((r:Row)=><tr key={r.id}><td>{dateLabel(r.at,'Asia/Shanghai')}{r.reconstructed&&<small>历史还原</small>}</td><td>{cash(r.net)}</td><td>{cash(r.held_value)}</td><td>{signedCash(r.difference)}</td></tr>)}
          </tbody></table></div>}
        </details>
      </section>
      <section className="t-review-scenario">
        <div className="t-review-heading"><h3>资金复用情景</h3><span className="t-review-tag">假设对照</span></div>
        {ready?<>
          <strong className="t-review-amount">{signedCash(scenario.difference)} <small>元</small></strong><p>操作情景相对不卖出持有的差额</p>
          <div className="t-review-values"><div><span>操作情景资产</span><strong>¥ {cash(scenario.actual_value)}</strong></div><div><span>不卖出持有参照</span><strong>¥ {cash(scenario.hold_value)}</strong></div></div>
        </>:<p>{scenario?.status==='NOT_STARTED'?'建立当前起点后，观察后续操作与不卖出持有的差异。':scenario?.status==='WAIT_BASELINE'?'待已有在途完成后自动建立参照。':'存在影响公平比较的交易，暂不输出精确增益。'}</p>}
        {(scenario?.reasons||[]).map((r:Row)=><p key={r.code}>{r.message}</p>)}
        <details><summary>范围与资金假设</summary>
          <p>{review.notice}</p>
          <p>双方从同一份额起步。卖出净款确认后列为应收，实际到账记录后才可在情景中复用；买入先使用虚拟余额，不足部分才记为双方相同的新增投入。参照在相同净值下投入这部分资金，并按本笔实际取得份额比例分摊买入费用与取整。不追溯基准前的盈利，不计现金利息。</p>
          {baseline?.start_pending?<p>已于 {dateLabel(baseline.requested_at,'Asia/Shanghai',true)} 申请；比较从实际自动建立时开始，等待期间的交易不会计成新增增益。</p>:baseline&&<p>起点 {dateLabel(baseline.started_at,'Asia/Shanghai',true)} · {quantity(baseline.units)} 份 · 净值 {quantity(baseline.nav,4)}{baseline.is_estimate?'（估算）':'（正式）'}。</p>}
          {ready&&<p>情景虚拟余额 ¥ {cash(scenario.virtual_cash)} · 赎回应收 ¥ {cash(scenario.receivable)} · 新增投入 ¥ {cash(scenario.external_contributions)}。这些数值不表示实际钱包余额。</p>}
          {!!baseline?.previous?.length&&<p>已保留 {baseline.previous.length} 份先前起点摘要。</p>}
          <button type="button" onClick={start} disabled={busy||revision==null||baseline?.start_pending}>{busy?'正在建立…':baseline?.start_pending?'等待自动建立':baseline?'从当前重新建立基准':'从当前建立基准'}</button>
        </details>
        {error&&<p role="alert">{error}</p>}
      </section>
    </div>
  </details>;
}
