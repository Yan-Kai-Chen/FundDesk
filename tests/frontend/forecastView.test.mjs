import test from 'node:test';
import assert from 'node:assert/strict';
import { forecastNumber, forecastPercent, forecastError, forecastFor, forecastScore, forecastRecent, forecastReady, forecastWaiting,
  forecastTestVerdict, forecastInterval, forecastStages, forecastResolvedMetrics, forecastSelectionNote } from '../../src/frontend/src/forecastView.ts';

test('ratio errors and explicit percentage-point errors retain their units', () => {
  assert.equal(forecastPercent('0.0123', true), '+1.23%');
  assert.equal(forecastError('0.0123'), '1.23 个百分点');
  assert.equal(forecastError('1.23', true), '1.23 个百分点');
  assert.equal(forecastError('-0.001', false, true), '-0.10 个百分点');
});
test('missing and malformed metrics never turn into zero or valid evidence', () => {
  for (const value of [null, undefined, '', '  ', false, {}, 'NaN', 'Infinity']) {
    assert.equal(forecastNumber(value), null); assert.equal(forecastError(value), '—');
  }
  assert.equal(forecastPercent('0'), '0.00%');
});
test('fund and horizon selection cannot cross-contaminate forecasts', () => {
  const data = { latest: { funds: [{ fund_id: 'a', forecasts: [{ horizon: 1, forecast_return: '0.01' }, { horizon: 5, forecast_return: '0.02' }] }, { fund_id: 'b', forecasts: [{ horizon: 1, forecast_return: '-0.01' }] }] } };
  assert.equal(forecastFor(data, 'a', 5).forecast_return, '0.02');
  assert.equal(forecastFor(data, 'b', 1).forecast_return, '-0.01');
  assert.equal(forecastFor(data, 'b', 5), undefined);
});
test('formal scores remain separated by engine version, fund and horizon', () => {
  const data = { latest: { engine_version: 'new' }, scores: [
    { fund_id: 'a', horizon: 1, engine_version: 'old', resolved: 100 },
    { fund_id: 'a', horizon: 5, engine_version: 'new', resolved: 3 },
    { fund_id: 'b', horizon: 1, engine_version: 'new', resolved: 7 },
    { fund_id: 'a', horizon: 1, engine_version: 'new', resolved: 1 },
  ] };
  assert.equal(forecastScore(data, 'a', 1).resolved, 1);
  assert.equal(forecastScore({ ...data, latest: null }, 'a', 1), undefined);
});
test('recent forward checks are selected by model version and horizon, sorted and bounded without altering source', () => {
  const rows = Array.from({ length: 15 }, (_, index) => ({ prediction_id: String(index), fund_id: 'a', horizon: 1, engine_version: 'new', generated_at: `2026-10-${String(index + 1).padStart(2, '0')}T06:00:00Z` }));
  const data = { latest: { engine_version: 'new' }, recent_predictions: [...rows,
    { fund_id: 'a', horizon: 1, engine_version: 'old', generated_at: '2026-10-23T06:00:00Z' },
    { fund_id: 'a', horizon: 5, engine_version: 'new', generated_at: '2026-10-23T06:00:00Z' }] };
  const before = structuredClone(data);
  const selected = forecastRecent(data, 'a', 1);
  assert.equal(selected.length, 12); assert.equal(selected[0].prediction_id, '14');
  assert.equal(selected.at(-1).prediction_id, '3'); assert.deepEqual(data, before);
  assert.deepEqual(forecastRecent(data, 'b', 1), []);
});
test('zero baseline is a valid point forecast, distinct from waiting or upward signal', () => {
  const forecast = { status: 'READY', forecast_return: '0', selected_model: 'ZERO' };
  assert.equal(forecastReady(forecast), true);
  assert.equal(forecastWaiting({}, forecast), '等待正式净值');
  assert.match(forecastSelectionNote(forecast), /未胜上一值基线/);
  assert.equal(forecastReady({ ...forecast, status: 'WAIT' }), false);
  assert.equal(forecastReady({ status: 'READY', forecast_return: null }), false);
});
test('final tests which did not improve cannot be presented as successful model selection', () => {
  assert.equal(forecastTestVerdict({ test: { count: 25, improvement: '-0.1', improved_over_baseline: false } }), '最终测试未优于基线');
  assert.equal(forecastTestVerdict({ test: { count: 25, improvement: '0.1', improved_over_baseline: false } }), '最终测试未优于基线');
  assert.equal(forecastTestVerdict({ test: { count: 25, improvement: '0.1', improved_over_baseline: true } }), '最终测试误差减少 10.00%');
  assert.equal(forecastTestVerdict({ test: { count: 0 } }), '等待测试');
});
test('no forward samples means no displayed zero error or coverage, even when source sent zero', () => {
  assert.deepEqual(forecastResolvedMetrics({ resolved: 0, pending: 6, mae_pp: '0', baseline_mae_pp: '0', interval_coverage: '0' }),
    { resolved: 0, pending: 6, error: '—', baselineError: '—', coverage: '—' });
  assert.equal(forecastResolvedMetrics({ resolved: 1, mae_pp: '0', interval_coverage: '1' }).error, '0.00 个百分点');
});
test('experimental intervals require both bounds and valid ordering', () => {
  assert.equal(forecastInterval({ lower_return: '-0.02', upper_return: '0.03' }), '-2.00% 至 +3.00%');
  assert.equal(forecastInterval({ lower_return: '0', upper_return: null }), '—');
  assert.equal(forecastInterval({ lower_return: '0.03', upper_return: '-0.02' }), '—');
});
test('stage dates come from explicit evidence, never from inferred history boundaries', () => {
  const stages = forecastStages({ history: { start: '2020-01-01', end: '2026-10-23', train: { start: '2020-02-01', end: '2024-01-01', count: 900 } },
    validation: { start: '2024-01-02', end: '2024-10-01', count: 100 }, calibration: { count: 100 }, test: { start: '2025-01-01', end: '2026-10-20', count: 200 } });
  assert.equal(stages[0].start, '2020-02-01');
  assert.equal(stages[2].start, undefined);
  assert.equal(stages[3].count, 200);
});
test('waiting reasons preserve human evidence but do not expose unknown implementation codes', () => {
  assert.equal(forecastWaiting({ reasons: [{ code: 'UNKNOWN', message: '历史样本不足' }] }), '历史样本不足');
  assert.equal(forecastWaiting({}, { status: 'WAIT', message: '等待当天正式基准' }), '等待当天正式基准');
  assert.equal(forecastWaiting({ reasons: ['MODEL_SAMPLES_MISSING'] }), '资料不足，暂未生成');
  assert.equal(forecastWaiting({}, { status: 'READY', forecast_return: '0.01', resolution: {} }), '已核对');
});
