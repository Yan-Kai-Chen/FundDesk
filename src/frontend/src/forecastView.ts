import type { Row } from './api';

export function forecastNumber(value: unknown): number | null {
  if (value == null || typeof value === 'boolean' || (typeof value === 'string' && !value.trim())) return null;
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function forecastPercent(value: unknown, signed = false): string {
  const number = forecastNumber(value);
  return number == null ? '—' : `${signed && number > 0 ? '+' : ''}${(number * 100).toFixed(2)}%`;
}

// Most model errors are return ratios; only fields explicitly ending in _pp
// are already percentage points. Keep those two units separate at the view.
export function forecastError(value: unknown, alreadyPoints = false, signed = false): string {
  const number = forecastNumber(value);
  if (number == null) return '—';
  const points = alreadyPoints ? number : number * 100;
  return `${signed && points > 0 ? '+' : ''}${points.toFixed(2)} 个百分点`;
}

export function forecastFor(forecasting: Row | null | undefined, fundId: string, horizon: number): Row | undefined {
  return forecasting?.latest?.funds?.find((fund: Row) => fund.fund_id === fundId)
    ?.forecasts?.find((forecast: Row) => Number(forecast.horizon) === horizon);
}

export function forecastScore(forecasting: Row | null | undefined, fundId: string, horizon: number): Row | undefined {
  const version = forecasting?.latest?.engine_version;
  if (!version) return undefined;
  return forecasting?.scores?.find((score: Row) => score.fund_id === fundId &&
    Number(score.horizon) === horizon && score.engine_version === version);
}

export function forecastRecent(forecasting: Row | null | undefined, fundId: string, horizon: number): Row[] {
  const version = forecasting?.latest?.engine_version;
  if (!version) return [];
  const rows: Row[] = forecasting?.recent_predictions || [];
  return rows.map((row, index) => ({ row, index })).filter(({ row }) => row.fund_id === fundId &&
    Number(row.horizon) === horizon && row.engine_version === version)
    .sort((a, b) => String(b.row.generated_at || '').localeCompare(String(a.row.generated_at || '')) || a.index - b.index)
    .slice(0, 12).map(({ row }) => row);
}

export function forecastReady(forecast?: Row): boolean {
  return !!forecast && forecast.status === 'READY' && forecastNumber(forecast.forecast_return) != null;
}

export function forecastWaiting(fundResult?: Row, forecast?: Row): string {
  if (forecastReady(forecast)) return forecast?.resolution ? '已核对' : '等待正式净值';
  const direct = forecast?.message || forecast?.selection_reason;
  if (typeof direct === 'string' && /[\u3400-\u9fff]/.test(direct)) return direct;
  const reasons = forecast?.reasons || fundResult?.reasons || [];
  const first = Array.isArray(reasons) ? reasons[0] : reasons;
  const message = typeof first === 'object' && first ? first.message : first;
  return typeof message === 'string' && /[\u3400-\u9fff]/.test(message) ? message : '资料不足，暂未生成';
}

export function forecastTestVerdict(forecast?: Row): string {
  const result = forecast?.test;
  if (!result || !(Number(result.count) > 0)) return '等待测试';
  const improvement = forecastNumber(result.improvement);
  if (result.improved_over_baseline === false || improvement == null || improvement <= 0)
    return '最终测试未优于基线';
  return `最终测试误差减少 ${forecastPercent(improvement)}`;
}

export function forecastModelLabel(forecast?: Row): string {
  if (!forecast) return '尚未生成';
  return forecast.model_label || (forecast.selected_model === 'ZERO' ? '零收益基线' : '已保存模型');
}

export function forecastSelectionNote(forecast?: Row): string {
  if (!forecast) return '';
  if (forecast.selected_model === 'ZERO') return '验证期未胜上一值基线，方向证据不足；0.00% 是基线点估计，不表示基金将保持不动。';
  return '模型由验证段选择，最终测试段独立保留；历史表现不保证今后误差。';
}

export function forecastInterval(forecast?: Row): string {
  const lower = forecastNumber(forecast?.lower_return), upper = forecastNumber(forecast?.upper_return);
  return lower == null || upper == null || lower > upper ? '—' : `${forecastPercent(lower, true)} 至 ${forecastPercent(upper, true)}`;
}

export function forecastStages(forecast?: Row): { id: string; label: string; start?: string; end?: string; count?: number }[] {
  if (!forecast) return [];
  const history = forecast.history || {};
  const train = history.train || history.training || forecast.train || {
    start: history.train_start || history.training_start,
    end: history.train_end || history.training_end,
    count: history.train_count || history.training_count,
  };
  return [['train', '训练', train], ['validation', '选型验证', forecast.validation],
    ['calibration', '区间校准', forecast.calibration], ['test', '最终测试', forecast.test]]
    .map(([id, label, stage]) => ({ id: String(id), label: String(label),
      start: (stage as Row | undefined)?.start, end: (stage as Row | undefined)?.end,
      count: (stage as Row | undefined)?.count }));
}

export function forecastResolvedMetrics(score?: Row): { resolved: number; pending: number; error: string; baselineError: string; coverage: string } {
  const resolved = Math.max(0, Number(score?.resolved) || 0), pending = Math.max(0, Number(score?.pending) || 0);
  return { resolved, pending,
    error: resolved ? forecastError(score?.mae_pp, true) : '—',
    baselineError: resolved ? forecastError(score?.baseline_mae_pp, true) : '—',
    coverage: resolved ? forecastPercent(score?.interval_coverage) : '—' };
}
