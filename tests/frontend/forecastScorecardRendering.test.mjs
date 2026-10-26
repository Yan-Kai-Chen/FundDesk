import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const front = new URL('../../src/frontend/', import.meta.url), req = createRequire(new URL('package.json', front));
const ts = req('typescript'), React = req('react'), { renderToStaticMarkup } = req('react-dom/server');
const compiled = new Map();
function componentURL(name) {
  if (compiled.has(name)) return compiled.get(name);
  const ext = existsSync(new URL(`src/${name}.tsx`, front)) ? 'tsx' : 'ts';
  const source = readFileSync(new URL(`src/${name}.${ext}`, front), 'utf8').replace(/^import ['"].*\.css['"];?\s*$/gm, '');
  let js = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from (['"])([^'"]+)\1/g, (_, q, s) => `from ${q}${s.startsWith('./') ? componentURL(s.slice(2)) : pathToFileURL(req.resolve(s)).href}${q}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`; compiled.set(name, url); return url;
}
const { ForecastScorecard } = await import(componentURL('ForecastScorecard'));
const render = value => renderToStaticMarkup(React.createElement(ForecastScorecard, { value }));
const base = () => ({ engine_version: 'v1', summary: { resolved: 0, pending: 1 }, recent_limit: 20,
  direction_note: '预测和实际均非零时比较；零预测为未给方向、实际平盘不计分母。',
  notice: '少量或重叠样本不能证明稳定有效。', deduplication: '首次冻结预测，首次正式核对。',
  groups: [{ engine_version: 'v1', fund_id: 'f', fund_code: '012183', name: '沪港深', horizon: 1, resolved: 0, pending: 1,
    mae_pp: null, baseline_mae_pp: null, direction_accuracy: null, direction_count: 0, verdict: 'NO_RESULT', recent_resolved: [] }],
  not_in_experiment: [{ fund_code: '006810', name: '香港银行' }] });

test('folded card distinguishes waiting from zero error and unsupported fund from failed model', () => {
  const html = render(base());
  assert.match(html, /预测持续成绩单/); assert.match(html, /已核对 0 · 待结果 1/);
  assert.match(html, /等待正式结果/); assert.match(html, /尚无合格正式结果/); assert.match(html, /无非零方向样本/);
  assert.match(html, /006810/); assert.match(html, /未纳入六基金预测实验/);
  assert.doesNotMatch(html, /<details[^>]*open|NaN|undefined|100\.00%/);
});

test('original frozen prediction formal outcome and baseline remain visible together', () => {
  const value = base(); value.summary.resolved = 1;
  Object.assign(value.groups[0], { resolved: 1, pending: 0, mae_pp: '1', baseline_mae_pp: '2', verdict: 'IMPROVED',
    direction_accuracy: '1', direction_count: 1, direction_correct_count: 1,
    recent_resolved: [{ prediction_id: 'p1', target_date: '2026-10-23', generated_at: '2026-10-22T12:00:00Z',
      forecast_return: '.01', actual_return: '.02', baseline_return: '0', absolute_error_pp: '1', model_label: '原冻结模型' }] });
  const html = render(value);
  assert.match(html, /\+1\.00%/); assert.match(html, /\+2\.00%/); assert.match(html, /0\.00%/);
  assert.match(html, /1\.00 个百分点/); assert.match(html, /误差较基线小/); assert.match(html, /1 \/ 1 个方向样本/);
  assert.match(html, /原冻结模型/); assert.match(html, /首次正式核对/);
  assert.match(html, /最多显示最近 20 条/); assert.doesNotMatch(html, /已证明有效|保证盈利/);
});

test('zero forecast abstains on direction and historical final test stays separately labeled', () => {
  const value = base();
  Object.assign(value.groups[0], { resolved: 1, pending: 0, mae_pp: '2', baseline_mae_pp: '2', verdict: 'TIED' });
  value.historical_tests = [{ fund_id: 'f', horizon: 1, engine_version: 'v1', count: 100, mae_pp: '1.5', baseline_mae_pp: '1',
    start: '2026-01-01', end: '2026-06-30', notice: '不计入前向成绩，测试后不据此改选模型。' }];
  const html = render(value);
  assert.match(html, /与基线持平/); assert.match(html, /零预测为未给方向/);
  assert.match(html, /回顾性最终测试/); assert.match(html, /100 个样本/); assert.match(html, /不计入前向成绩/);
});

test('loading is a read state and never advertises automatic refitting', () => {
  const html = render(undefined);
  assert.match(html, /正在读取/); assert.match(html, /不会重新拟合/);
  assert.doesNotMatch(html, /生成预测|更新并拟合/);
});
