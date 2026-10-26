import { useState } from 'react';
import type { Row } from './api';
import { dateLabel } from './api';
import { forecastError, forecastPercent } from './forecastView';
import './forecastScorecard.css';

type Props = { value?: Row; onSelectFund?: (fundId: string) => void };
const verdict = (row: Row) => ({ IMPROVED: '误差较基线小', TIED: '与基线持平', WORSE: '误差较基线大', NO_RESULT: '等待正式结果' }[String(row.verdict)] || '等待正式结果');

export function ForecastScorecard({ value, onSelectFund }: Props) {
  const [horizon, setHorizon] = useState(1), [selected, setSelected] = useState('');
  const [engine, setEngine] = useState('');
  const groups: Row[] = value?.groups || [];
  const engines = [...new Set(groups.map(row => String(row.engine_version)))];
  const selectedEngine = engines.includes(engine) ? engine : String(value?.engine_version || engines[0] || '');
  const rows = groups.filter(row => row.engine_version === selectedEngine && row.horizon === horizon);
  const active = rows.find(row => row.fund_id === selected) || rows.find(row => row.resolved > 0) || rows[0];
  const recent: Row[] = active?.recent_resolved || [];
  const historical = value?.historical_tests?.find((row: Row) => row.fund_id === active?.fund_id && row.horizon === horizon && row.engine_version === selectedEngine);
  const pick = (row: Row) => { setSelected(row.fund_id); onSelectFund?.(row.fund_id); };

  return <details className="panel forecast-scorecard">
    <summary><span>预测持续成绩单</span><span className="pill sage">{value ? `已核对 ${value.summary?.resolved ?? 0} · 待结果 ${value.summary?.pending ?? 0}` : '正在读取'}</span></summary>
    <div className="scorecard-body">
      {!value ? <p>读取已保存预测，不会重新拟合。</p> : <>
        <div className="scorecard-toolbar"><div className="section-tabs" role="group" aria-label="成绩单期限">
          {[[1, '下一参考日'], [5, '未来 5 个参考日']].map(([id, name]) => <button type="button" key={id} className={horizon === id ? 'active' : ''} aria-pressed={horizon === id} onClick={() => setHorizon(Number(id))}>{name}</button>)}
        </div>{engines.length > 1 ? <label>模型版本 <select value={selectedEngine} onChange={event => setEngine(event.target.value)}>{engines.map(item => <option key={item}>{item}</option>)}</select></label> : <span>{selectedEngine}</span>}</div>
        <p>比较当时预测、后来正式总回报与零收益基线。各基金和期限分别累计；当前样本仅作持续核对。</p>
        <div className="table-wrap"><table className="scorecard-table"><thead><tr><th>基金</th><th>已核对 / 待结果</th><th>预测 MAE</th><th>零基线 MAE</th><th>比较</th><th>方向命中</th></tr></thead><tbody>
          {rows.map(row => <tr key={`${row.engine_version}-${row.fund_id}-${row.horizon}`} className={active?.fund_id === row.fund_id ? 'scorecard-selected' : ''}>
            <td><button type="button" className="scorecard-fund" aria-pressed={active?.fund_id === row.fund_id} onClick={() => pick(row)}>{row.name || row.fund_code}</button><small>{row.fund_code}</small></td>
            <td>{row.resolved} / {row.pending}{row.unverified_result_count > 0 && <small>{row.unverified_result_count} 条结果依据待核实</small>}</td>
            <td>{forecastError(row.mae_pp, true)}</td><td>{forecastError(row.baseline_mae_pp, true)}</td>
            <td><span className={`pill ${row.verdict === 'IMPROVED' ? 'sage' : 'khaki'}`}>{verdict(row)}</span></td>
            <td>{forecastPercent(row.direction_accuracy)}<small>{row.direction_count ? `${row.direction_correct_count} / ${row.direction_count} 个方向样本` : '无非零方向样本'}</small></td>
          </tr>)}
        </tbody></table></div>
        {active && <div className="scorecard-current">
          <h3>{active.name || active.fund_code} · 最近核对</h3>
          {recent.length ? <><div className="table-wrap"><table className="scorecard-history"><thead><tr><th>目标日 / 冻结时间</th><th>当时预测</th><th>正式总回报</th><th>零收益基线</th><th>绝对误差</th></tr></thead><tbody>
            {recent.map(row => <tr key={row.prediction_id}><td>{row.target_date}<small>{dateLabel(row.generated_at, 'Asia/Shanghai', true)} 北京时间</small></td><td>{forecastPercent(row.forecast_return, true)}<small>{row.model_label || row.selected_model || '已保存模型'}</small></td><td>{forecastPercent(row.actual_return, true)}<small>首次正式核对</small></td><td>{forecastPercent(row.baseline_return)}</td><td>{forecastError(row.absolute_error_pp, true)}</td></tr>)}
          </tbody></table></div><p>最多显示最近 {value.recent_limit || 20} 条；累计误差使用本组全部合格样本。{active.overlapping_targets && '5 日目标窗口可能重叠，不能当作独立样本。'}</p></> : <p>尚无合格正式结果，误差和方向命中保持未知。</p>}
          <p>{value.direction_note}</p>
          <details className="scorecard-method"><summary>回顾性最终测试与计分口径</summary><div>
            {historical ? <><p>本组最新冻结模型附带的回顾性最终测试：{historical.start || '—'} 至 {historical.end || '—'}，{historical.count} 个样本。</p><p>模型 MAE {forecastError(historical.mae_pp, true)}；零基线 MAE {forecastError(historical.baseline_mae_pp, true)}。</p><p>{historical.notice}</p></> : <p>本组没有已保存的回顾性最终测试。</p>}
            <p>MAE 为平均绝对误差，单位是收益率百分点；基线始终预测总回报为 0。</p><p>{value.deduplication}</p><p>{value.notice}</p>
          </div></details>
        </div>}
        {!!value.not_in_experiment?.length && <p className="scorecard-not-covered">{value.not_in_experiment.map((row: Row) => `${row.name || row.fund_code}（${row.fund_code}）`).join('、')}：未纳入六基金预测实验，行情与持仓正常使用。</p>}
      </>}
    </div>
  </details>;
}
