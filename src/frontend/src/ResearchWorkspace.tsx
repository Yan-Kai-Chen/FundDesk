import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ArrowRight, Check, Download, FileInput, Play, RefreshCw, Settings2 } from 'lucide-react';
import type { Row } from './api';
import { api, key, cash, pct, quantity, dateLabel } from './api';
import { sidebarFund } from './sidebarCatalog';
import { currentDayDisplay } from './viewData';
import { researchMethods, researchStatus, researchReason, directionLabel, selectionMatches, researchItem, rate } from './researchView';
import { ForecastWorkspace } from './ForecastWorkspace';
import { ForecastScorecard } from './ForecastScorecard';

export function ResearchWorkspace({ state, research, mode, selected, predictions, evaluation, forecasting, forecastScorecard, sidebar, onSaved, onSelect, onPolicy, onImport, onPrediction }: {
  state: Row; research: Row | null; mode: string; selected: string; predictions: Row[]; evaluation: Row | null;
  forecasting: Row | null; forecastScorecard?:Row|null; sidebar: ReactNode; onSaved: () => Promise<void>; onSelect: (id: string) => void; onPolicy: () => void; onImport: () => void; onPrediction: () => void;
}) {
  const [methods, setMethods] = useState(['TREND', 'VALUE', 'REBALANCE']);
  const [busy, setBusy] = useState(''), [error, setError] = useState('');
  const [viewed, setViewed] = useState<Row | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const storedRun = viewed && research?.latest && viewed.id === research.latest.id ? research.latest : viewed || research?.latest;
  const run = storedRun ? { ...storedRun, stale: storedRun.stale || !!research && storedRun.basis_hash !== research.current_basis_hash } : null;
  const fund = state.funds.find((f: Row) => f.id === selected);
  const presentation = fund ? sidebarFund(state.fund_sidebar, fund) : null;
  const quote = currentDayDisplay(fund);
  const selectedStats = run?.observations?.funds?.find((f: Row) => f.fund_id === selected);
  const named = (id: string) => {
    const f = state.funds.find((row: Row) => row.id === id);
    return f ? sidebarFund(state.fund_sidebar, f).display_name : ({ SYN_EQ_A: '合成股票 A', SYN_EQ_B: '合成股票 B', SYN_GOLD: '合成黄金' } as Row)[id] || id;
  };
  const perform = async (kind: string, task: (signal: AbortSignal) => Promise<unknown>) => {
    setBusy(kind); setError(''); controller.current = new AbortController();
    try { await task(controller.current.signal); await onSaved(); }
    catch (err) { if ((err as Error).name !== 'AbortError') setError((err as Error).message); }
    finally { setBusy(''); }
  };
  const forecastRows = predictions.filter(p => p.fund_id === selected);
  const score = research?.forecast_scores?.[selected];
  const engine = run?.engine_result || run?.target_result;
  const targetsOnly = !!run?.target_result && !run?.engine_result;
  return <div className="workbench-layout research-workbench">{sidebar}<div className="fund-main">
    <ForecastWorkspace key={mode} state={state} mode={mode} selected={selected} forecasting={forecasting} onSaved={onSaved} onSelect={onSelect}/>
    <ForecastScorecard value={forecastScorecard||undefined} onSelectFund={onSelect}/>
    <section className="panel research-control">
      <div className="research-section-heading"><div><h2>仓位方法</h2><p>选择方法，同一快照分别比较。</p></div><div className="button-group">
        <button className="icon-button" aria-label="研究目标与强度" title="研究目标与强度" disabled={!research} onClick={onPolicy}><Settings2 size={19}/></button>
        <button className="button" disabled={!selected} onClick={onPrediction}>登记预测</button>
      </div></div>
      <div className="research-method-options" role="group" aria-label="研究方法">{researchMethods.map(method => <button key={method.id} className={methods.includes(method.id) ? 'chosen' : ''} aria-pressed={methods.includes(method.id)} onClick={() => setMethods(old => old.includes(method.id) ? old.filter(id => id !== method.id) : [...old, method.id])}>
        <span>{method.label}{methods.includes(method.id) && <Check size={16}/>}</span><small>{method.description}</small>
        {run?.observations?.methods?.[method.id] && <small className="research-method-status">{researchStatus[run.observations.methods[method.id].status]} · {run.observations.methods[method.id].observation_ready}/{run.observations.methods[method.id].applicable_count} 只适用基金</small>}
      </button>)}</div>
      <div className="research-generate-row"><span className="pill khaki">{mode === 'demo' ? '合成演示' : `研究强度 · ${research?.levels?.[research.policy.level]?.label || '观察'}`}</span><div className="button-group">
        {mode === 'real' && <button className="button" disabled={!!busy} onClick={() => void perform('data', signal => api('/research/data/refresh', mode, {}, undefined, signal))}><RefreshCw size={17}/>{busy === 'data' ? '更新资料中…' : '更新研究资料'}</button>}
        <button className="primary" disabled={!!busy || !methods.length} onClick={() => void perform('run', async signal => { const value = await api('/research/runs', mode, { methods, key: key() }, undefined, signal); setViewed(value); })}><Play size={17}/>{busy === 'run' ? '计算中…' : '生成本次分析'}</button>
      </div></div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!research && <p className="research-note">正在读取研究资料…</p>}
    </section>

    {run ? <>
      <div className="research-run-heading"><div><span>{dateLabel(run.created_at, 'Asia/Shanghai', true)} 北京时间</span><span className={`pill ${run.stale || !selectionMatches(methods, run) ? 'khaki' : 'sage'}`}>{run.stale ? '资料有更新，请重新生成' : !selectionMatches(methods, run) ? '方法选择已改变' : viewed && viewed.id !== research?.latest?.id ? '历史分析' : '已保存快照'}</span></div>
        <div className="button-group"><select aria-label="查看研究记录" value={run.id} disabled={!!busy} onChange={e => void perform('history', async signal => { setViewed(await api(`/research/runs/${e.target.value}`, mode, undefined, undefined, signal)); })}>
          {(research?.history?.length ? research.history : [{id:run.id, created_at:run.created_at}]).map((r:Row) => <option key={r.id} value={r.id}>{dateLabel(r.created_at, 'Asia/Shanghai', true)}</option>)}
        </select><a className="icon-button" aria-label="导出本次研究" href={`/api/v1/research/runs/${run.id}/export?mode=${mode}`} download><Download size={18}/></a></div>
      </div>
      <div className="research-overview"><div><span>趋势锚点</span><strong>{run.observations.trend_anchor || '待补日历'}</strong></div><div><span>组合历史年化波动</span><strong>{rate(run.observations.portfolio_risk.annualized_volatility)}</strong></div><div><span>研究范围</span><strong>{mode === 'demo' ? '合成样本' : `${state.funds.length} 只登记基金`}</strong></div></div>
      {mode === 'demo' && <p className="research-note">三方法完整演算使用独立的三只合成资产；样本预算与日期均不代表你的账户。</p>}

      <section className="panel"><div className="panel-heading"><div><h2>{presentation?.display_name || '选择基金'}</h2><p className="research-note">当前行情 {quote.date} · {quote.ready ? `${pct(quote.change)} · ${quote.label}` : quote.label}{selectedStats?.history_end && ` · 历史观察截至 ${selectedStats.history_end}`}</p></div>{presentation?.theme && <span className="pill sage">{presentation.theme}</span>}</div>
        <div className="table-wrap"><table className="research-comparison"><thead><tr><th>方法</th><th>观察结果</th><th>状态</th></tr></thead><tbody>{run.selected_methods.map((method: string) => {
          const item = researchItem(run, method, selected);
          return <tr key={method}><td><strong>{researchMethods.find(m => m.id === method)?.label}</strong></td><td>
            {method === 'TREND' && item?.total_return != null ? <><strong>252日总回报 {pct(item.total_return)}</strong><small>相对现金参照 {pct(item.excess_vs_cash)} · {item.selected == null ? '排名待完整资料' : item.selected ? '进入前二候选' : '未进入前二候选'}</small></> : method === 'REBALANCE' && item?.gap != null ? <><strong>当前 {rate(item.current_weight)} → 目标 {rate(item.target_weight)}</strong><small>配比差 {pct(item.gap)}，金额仍需完整执行证据</small></> : <span>{item?.reasons?.length ? researchReason(item.reasons[0]) : '查看下方独立方案'}</span>}
          </td><td><span className={`pill ${item?.status === 'OBSERVATION_READY' ? 'sage' : ''}`}>{researchStatus[item?.status] || '方法样例'}</span></td></tr>;
        })}</tbody></table></div>
        <div className="research-stat-grid">{[['近20期收益', selectedStats?.stats.return_20], ['近60期收益', selectedStats?.stats.return_60], ['年化波动 · 60期', selectedStats?.stats.volatility_60], ['最大回撤 · 252期', selectedStats?.stats.drawdown_252]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{label === '年化波动 · 60期' ? rate(value) : pct(value)}</strong></div>)}</div>
      </section>

      {engine ? <section className="panel"><div className="panel-heading"><h2>{mode === 'demo' ? '合成方法样例' : targetsOnly ? '基金范围目标比较' : '独立研究方案'}</h2><span className="pill khaki">{targetsOnly ? '仅比较配比 · 未生成金额' : '三份预算分别计算，不相加'}</span></div><div className="section-body research-engine-results">
        {targetsOnly && <p className="research-note">只比较已登记基金与显式研究预算的目标，不声明你的账户现金或申赎开放。底仓保持保护。</p>}
        <p className="research-note">参考价格截至 {engine.decision_session}。方向表示目标配比偏离，不表示上涨概率；金额与份额均为研究参考。</p>
        {run.selected_methods.map((id: string) => engine.methods[id]).filter(Boolean).map((method: any) => <details key={method.method} open className="research-engine-method"><summary>{method.label}<span className="pill">{researchStatus[method.analysis_status] || method.analysis_status}</span></summary>
          {method.risk?.target_annualized_volatility != null && <p className="research-note">目标年化波动 {rate(method.risk.target_annualized_volatility)} · 风险上限 {rate(method.risk.volatility_limit)}</p>}
          {!!method.reasons?.length && <p className="research-note">{method.reasons.map(researchReason).join('；')}</p>}
          {!!method.items?.length && <div className="table-wrap"><table><thead><tr><th>基金</th><th>方向 / 状态</th><th>目标配比</th><th>参考金额 / 份额</th></tr></thead><tbody>{method.items.map((item: Row) => <tr key={item.asset_id}><td>{named(item.asset_id)}</td><td>{directionLabel[item.signal_action] || '不适用'}<small>{researchStatus[item.execution_status] || item.execution_status}{item.reasons?.length ? ` · ${researchReason(item.reasons[0])}` : ''}</small></td><td>{rate(item.target_weight)}</td><td>{item.execution_status === 'REFERENCE_ONLY' ? <>¥{cash(item.gross_reference)}<small>{quantity(item.units_reference)} 份 · 费用 ¥{cash(item.fee_reference)}</small></> : '—'}</td></tr>)}</tbody></table></div>}
        </details>)}
        {engine.intraday_scenario?.status !== 'NOT_REQUESTED' && <p className="research-note">盘中情景：{researchStatus[engine.intraday_scenario.status] || engine.intraday_scenario.status}。与正式方案分开，不改写收益。</p>}
      </div></section> : <details className="panel research-evidence"><summary>完整方案还需哪些资料</summary><div className="section-body"><p>趋势观察已接入公开历史。估值布局需要至少60个完整月的指数估值；完整金额方案还需要目标配比、风险预算及可核实的申赎日历和批次。</p><p>你可以先观察，不必在此维护账户总金额。研究预算独立于日常买卖记录。</p><div className="button-group"><button className="button" onClick={onPolicy}>设置目标配比 <ArrowRight size={16}/></button><button className="button" onClick={onImport}><FileInput size={17}/>导入研究快照</button></div></div></details>}
    </> : <section className="panel research-welcome"><h2>建立第一份研究快照</h2><p>先更新研究资料，再生成分析。切换页面或刷新行情不会自动新增方案。</p><p>三方法分别展示观察与适用范围，正式金额方案始终保留其数据条件。</p></section>}

    <details className="panel forecast-legacy-baselines"><summary>原净值基线与手工预测</summary><section className="panel"><div className="panel-heading"><h2>净值预测跟踪</h2><button className="text-button" onClick={onPrediction}>添加预测 <ArrowRight size={16}/></button></div>
      <div className="research-stat-grid"><div><span>已保存预测</span><strong>{score?.count ?? forecastRows.length}</strong></div><div><span>正式净值已核对</span><strong>{score?.resolved || 0}</strong></div><div><span>平均绝对误差</span><strong>{quantity(score?.mae, 4)}</strong></div><div><span>平均百分比误差</span><strong>{rate(score?.mape)}</strong></div></div>
      <div className="table-wrap"><table><thead><tr><th>目标日期 / 方法</th><th>预测净值</th><th>正式净值</th><th>绝对百分比误差</th></tr></thead><tbody>{forecastRows.slice(0,12).map(p => <tr key={p.id}><td>{p.target_date}<small>{p.method === 'LAST_VALUE' ? '上一有效净值基线' : p.method === 'MEAN_RETURN_5' ? '五期均值基线' : p.source}</small></td><td>{quantity(p.predicted_nav, 4)}</td><td>{p.resolution ? quantity(p.resolution.actual.value, 4) : '等待披露'}</td><td>{p.resolution ? rate(p.resolution.absolute_percentage_error) : '—'}</td></tr>)}</tbody></table></div>
      {!forecastRows.length && <p className="research-empty">生成分析会为资料满足条件的基金登记净值基线，后续正式净值自动匹配。</p>}
      <details className="research-provenance"><summary>方法与时间证据</summary><p>净值基线用于建立可复核的预测对照，不是三方法的仓位建议。误差按已保存预测与后来披露值统计。</p><p>{evaluation?.status === 'RESEARCH_ONLY' || evaluation?.status === 'DEMO_EVALUATION' ? `历史时间推进样本 ${evaluation.fold_count} 个；历史MAE ${quantity(evaluation.mae, 4)}。` : '历史时点证据仍在积累，不把今天下载的历史当作过去已知数据。'}</p></details>
    </section></details>

    <details className="panel research-evidence"><summary>研究资料与来源</summary><div className="section-body"><div className="table-wrap"><table><thead><tr><th>基金</th><th>历史覆盖</th><th>取得时间</th></tr></thead><tbody>{research?.data?.map((row: Row) => <tr key={row.fund_id}><td>{named(row.fund_id)}</td><td>{row.history_count ? <>{row.history_count} 个净值日<small>{row.history_start} 至 {row.history_end}</small></> : '待更新'}</td><td>{row.observed_at ? dateLabel(row.observed_at, 'Asia/Shanghai', true) : '—'}{row.source_url && <small><a href={row.source_url} target="_blank" rel="noreferrer">公开来源</a></small>}</td></tr>)}</tbody></table></div>
      <p className="research-note">总回报由公开净值及分红字段重建；现金参照使用天弘余额宝货币万份收益，不代表你的余额宝持仓。历史重建不等于样本外盈利验证。</p>
      {research?.sync?.outcomes?.filter((r: Row) => r.status !== 'READY').map((row: Row) => <p className="research-note" key={row.fund_code}>{row.fund_code}：{row.message}</p>)}
      <p className="research-note">只保存最新精简历史与最近30份研究分析；预测及其核对结果保留。</p>
      <button className="button" onClick={onImport}><FileInput size={17}/>导入完整研究快照</button>
    </div></details>
  </div></div>;
}
