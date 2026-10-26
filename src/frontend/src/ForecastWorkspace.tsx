import { useEffect, useRef, useState } from 'react';
import { Download, RefreshCw } from 'lucide-react';
import type { Row } from './api';
import { api, cash, dateLabel, key, quantity } from './api';
import { sidebarFund } from './sidebarCatalog';
import { forecastError, forecastFor, forecastInterval, forecastModelLabel, forecastNumber, forecastPercent,
  forecastReady, forecastRecent, forecastResolvedMetrics, forecastScore, forecastSelectionNote, forecastStages, forecastTestVerdict, forecastWaiting } from './forecastView';
import './forecastWorkspace.css';

type Props = {
  state: Row; mode: string; selected: string; forecasting: Row | null;
  onSaved: () => Promise<void>; onSelect: (id: string) => void;
};

export function ForecastWorkspace({ state, mode, selected, forecasting, onSaved, onSelect }: Props) {
  const [horizon, setHorizon] = useState(1);
  const [tab, setTab] = useState('current');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const latest = forecasting?.latest;
  const funds: Row[] = state.funds || [];
  const fund = funds.find(row => row.id === selected);
  const current = forecastFor(forecasting, selected, horizon);
  const currentFund = latest?.funds?.find((row: Row) => row.fund_id === selected);
  const ready = forecastReady(current);
  const score = forecastResolvedMetrics(forecastScore(forecasting, selected, horizon));
  const recent = forecastRecent(forecasting, selected, horizon);
  const result = current?.test;
  const holding = current?.holdings_reference;
  const resolution = current?.resolution;
  const name = (row: Row) => sidebarFund(state.fund_sidebar, row).display_name;
  const coverage = forecastNumber(current?.calibration?.level);
  const intervalLabel = `${coverage == null ? '80' : (coverage * 100).toFixed(0)}% 校准目标的实验范围`;
  const generate = async () => {
    if (busy) return;
    setBusy(true); setError(''); controller.current = new AbortController();
    try {
      await api('/forecasting/runs', mode, { key: key(), refresh: true }, undefined, controller.current.signal);
      await onSaved();
    } catch (caught) { if ((caught as Error).name !== 'AbortError') setError((caught as Error).message); }
    finally { setBusy(false); }
  };
  return <section className="panel forecast-workspace" aria-label="六基金净值预测">
    <div className="forecast-heading"><div><h2>净值预测</h2><p>预测采用总回报口径；先看独立测试，再用正式结果核对。</p></div><div className="button-group">
      {latest?.id && <a className="icon-button" aria-label="导出预测与完整验证" title="导出预测与完整验证" href={`/api/v1/forecasting/runs/${encodeURIComponent(latest.id)}/export?mode=${encodeURIComponent(mode)}`} download><Download size={18}/></a>}
      <button type="button" className="primary" disabled={busy || !forecasting} onClick={() => void generate()}><RefreshCw size={17}/>{busy ? '更新与拟合中…' : `更新并拟合 ${funds.length} 只`}</button>
    </div></div>
    <div className="forecast-toolbar"><div className="section-tabs" role="group" aria-label="预测期限">
      {[[1, '下一参考日'], [5, '未来 5 个参考日']].map(([value, label]) => <button type="button" key={value} className={horizon === value ? 'active' : ''} aria-pressed={horizon === value} onClick={() => setHorizon(Number(value))}>{label}</button>)}
    </div><span>{latest?.created_at ? `${dateLabel(latest.created_at, 'Asia/Shanghai', true)} 北京时间` : forecasting ? '尚未生成' : '正在读取…'}{latest?.stale && ' · 新资料待更新'}</span></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="table-wrap"><table className="forecast-fund-table"><thead><tr><th>基金 / 目标日</th><th>预测涨跌</th><th>独立测试</th><th>正式核对</th></tr></thead><tbody>
      {funds.map(row => {
        const forecast = forecastFor(forecasting, row.id, horizon);
        const found = latest?.funds?.find((item: Row) => item.fund_id === row.id);
        const presentation = sidebarFund(state.fund_sidebar, row);
        return <tr key={row.id} className={selected === row.id ? 'forecast-selected' : ''}>
          <td><button type="button" className="forecast-fund-link" aria-pressed={selected === row.id} onClick={() => onSelect(row.id)}>{presentation.display_name}</button><small>{forecast?.target_date || '目标日待确定'}{presentation.theme && ` · ${presentation.theme}`}</small></td>
          <td className="numeric"><strong>{forecastReady(forecast) ? forecastPercent(forecast?.forecast_return, true) : '—'}</strong><small>{forecastModelLabel(forecast)}{forecast?.selected_model === 'ZERO' && ' · 方向证据不足'}</small></td>
          <td>{forecastTestVerdict(forecast)}<small>{Number(forecast?.test?.count) > 0 ? `${forecast!.test.count} 个滚动测试样本` : '资料就绪后拟合'}</small></td>
          <td><span className={`pill ${forecast?.resolution ? 'sage' : 'khaki'}`}>{forecastWaiting(found, forecast)}</span></td>
        </tr>;
      })}
    </tbody></table></div>
    {!latest && forecasting && <p className="forecast-message">点击“更新并拟合”生成六只基金的首轮预测；切换基金或刷新页面不会重复生成。</p>}

    {fund && <div className="forecast-detail">
      <div className="forecast-detail-heading"><h3>{name(fund)}</h3><div className="section-tabs" role="tablist" aria-label="预测查看内容">
        {[['current', '本次预测'], ['test', '历史验证'], ['resolved', '正式核对']].map(([id, label]) => <button type="button" id={`forecast-tab-${id}`} aria-controls="forecast-tab-panel" role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} key={id} onClick={() => setTab(id)}>{label}</button>)}
      </div></div>
      <div id="forecast-tab-panel" role="tabpanel" aria-labelledby={`forecast-tab-${tab}`}>
        {tab === 'current' && <>
          {ready ? <><div className="forecast-current-grid">
            <div className="forecast-point"><span>至 {current!.target_date} 的预测涨跌</span><strong>{forecastPercent(current!.forecast_return, true)}</strong><small>{forecastModelLabel(current)}</small></div>
            <div><span>{intervalLabel}</span><strong>{forecastInterval(current)}</strong><small>历史校准的变动范围，不是保证</small></div>
            <div><span>当前持仓的情景变化</span><strong>{forecastNumber(holding?.forecast_change) == null ? '—' : `¥ ${Number(holding?.forecast_change) > 0 ? '+' : ''}${cash(holding?.forecast_change)}`}</strong><small>{holding?.units != null ? `${quantity(holding.units)} 份 · 假定无分红拆分` : '持仓参考资料不足'}</small></div>
          </div><p className="forecast-caption">{forecastSelectionNote(current)}</p>
          <details className="forecast-evidence"><summary>净值参考与计算条件</summary><div>
            <p>起点 {current!.base_date} · 正式净值 {quantity(current!.base_nav, 4)}；目标日参考净值 {quantity(current!.predicted_nav_reference, 4)}。</p>
            <p>模型预测总回报。净值参考与持仓金额变化仅为“当前已确认份额保持不变、期间无分红拆分”的情景，不含期间买卖和交易费用，不改写账本收益。</p>
            {holding?.lower_change != null && holding?.upper_change != null && <p>同一持仓的实验范围：¥ {cash(holding.lower_change)} 至 ¥ {cash(holding.upper_change)}。</p>}
            <p>期限使用参考交易日，不承诺基金当日开放申赎。模型版本 {latest?.engine_version || '—'}；生成时间 {dateLabel(current!.generated_at || latest?.created_at, 'Asia/Shanghai', true)} 北京时间。</p>
          </div></details></> : <p className="forecast-message">{forecastWaiting(currentFund, current)}。已有资料会保留，补齐后可重新拟合。</p>}
        </>}
        {tab === 'test' && <>
          <div className="forecast-validation-title"><strong>回顾性滚动测试</strong><span className={`pill ${result?.improved_over_baseline ? 'sage' : 'khaki'}`}>{forecastTestVerdict(current)}</span></div>
          <div className="forecast-test-grid">
            <div><span>模型平均绝对误差</span><strong>{Number(result?.count) > 0 ? forecastError(result?.mae) : '—'}</strong></div>
            <div><span>上一值基线误差</span><strong>{Number(result?.count) > 0 ? forecastError(result?.baseline_mae) : '—'}</strong></div>
            <div><span>历史方向命中率</span><strong>{Number(result?.count) > 0 ? forecastPercent(result?.direction_accuracy) : '—'}</strong></div>
            <div><span>实验区间覆盖率</span><strong>{Number(result?.count) > 0 ? forecastPercent(result?.coverage) : '—'}</strong></div>
          </div>
          <p className="forecast-caption">验证段用于选型，校准段用于区间，最终测试段不参与两者。今天取得的历史资料按日期回顾，不等于过去可交易时已掌握这些资料。</p>
          <details className="forecast-evidence"><summary>日期划分与测试样本</summary><div><div className="table-wrap"><table><thead><tr><th>阶段</th><th>开始</th><th>结束</th><th>样本</th></tr></thead><tbody>{forecastStages(current).map(stage => <tr key={stage.id}><td>{stage.label}</td><td>{stage.start || '未提供'}</td><td>{stage.end || '未提供'}</td><td>{stage.count ?? '—'}</td></tr>)}</tbody></table></div>
            <p>最终测试样本 {result?.count ?? '—'}；非重叠窗口 {result?.nonoverlap_count ?? '—'}{result?.overlapping_targets ? '。多日期限的目标窗口有重叠，样本之间不独立。' : '。'}</p>
            <p>均方根误差 {forecastError(result?.rmse)}；平均偏差 {forecastError(result?.mean_bias, false, true)}。最终测试没有改善时，仍按原结果如实保留。</p>
          </div></details>
        </>}
        {tab === 'resolved' && <>
          <div className="forecast-test-grid"><div><span>同版本已正式核对</span><strong>{score.resolved} 次</strong></div><div><span>等待披露</span><strong>{score.pending} 次</strong></div><div><span>前向平均绝对误差</span><strong>{score.error}</strong></div><div><span>同期基线误差</span><strong>{score.baselineError}</strong></div></div>
          <div className="forecast-forward-result"><span>本次目标日 {current?.target_date || '—'}</span>{resolution ? <><strong>正式涨跌 {forecastPercent(resolution.actual_return, true)}</strong><span>预测 {forecastPercent(current?.forecast_return, true)} · 绝对误差 {forecastError(resolution.absolute_error_pp, true)}</span><span>实验区间{resolution.interval_covered == null ? '覆盖情况待核对' : resolution.interval_covered ? '覆盖本次结果' : '未覆盖本次结果'}</span></> : <strong>{ready ? '等待正式净值披露后自动核对' : '尚无本次预测'}</strong>}</div>
          {!!recent.length && <details className="forecast-evidence"><summary>最近 {recent.length} 条保存与核对记录</summary><div className="table-wrap"><table><thead><tr><th>目标日 / 生成时间</th><th>预测涨跌</th><th>正式涨跌</th><th>绝对误差</th></tr></thead><tbody>{recent.map((row, index) => <tr key={row.prediction_id || index}><td>{row.target_date}<small>{dateLabel(row.generated_at, 'Asia/Shanghai', true)} 北京时间</small></td><td>{forecastPercent(row.forecast_return, true)}</td><td>{row.resolution ? forecastPercent(row.resolution.actual_return, true) : '等待披露'}</td><td>{row.resolution ? forecastError(row.resolution.absolute_error_pp, true) : '—'}</td></tr>)}</tbody></table></div></details>}
          <p className="forecast-caption">只统计保存后取得的正式结果，按模型版本和期限分别累计；{score.resolved ? `目前实验区间覆盖率 ${score.coverage}。` : '尚无已核对样本，暂不显示误差或成功率。'}原始预测不会随正式结果改写。</p>
        </>}
      </div>
    </div>}
    <div className="forecast-maintenance"><span>{forecasting?.maintenance?.message || '新正式净值取得后继续核对已保存预测。'}</span>{forecasting?.maintenance?.last_check_at && <span>最近检查 {dateLabel(forecasting.maintenance.last_check_at, 'Asia/Shanghai', true)} 北京时间</span>}</div>
  </section>;
}
