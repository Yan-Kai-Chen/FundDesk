import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const front = new URL('../../src/frontend/', import.meta.url), req = createRequire(new URL('package.json', front));
const ts = req('typescript'), React = req('react'), { renderToStaticMarkup } = req('react-dom/server');
const cache = new Map();
function sourceURL(name) {
  if (cache.has(name)) return cache.get(name);
  const ext = existsSync(new URL(`src/${name}.tsx`, front)) ? 'tsx' : 'ts';
  const source = readFileSync(new URL(`src/${name}.${ext}`, front), 'utf8').replace(/^import ['"].*\.css['"];?\s*$/gm, '');
  let js = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  js = js.replace(/from (['"])([^'"]+)\1/g, (_, q, s) => `from ${q}${s.startsWith('./') ? sourceURL(s.slice(2)) : pathToFileURL(req.resolve(s)).href}${q}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`; cache.set(name, url); return url;
}
const { NumberExplanationContent } = await import(sourceURL('NumberExplanation'));
const render = value => renderToStaticMarkup(React.createElement(NumberExplanationContent, { value }));
const base = () => ({ scope: 'FUND', name: '合成基金', fund_code: 'SYN001', components: { pnl: '20', value: '1020', cost: '1000',
  realized: '0', unrealized: '20', official_pnl: '10', mark_adjustment: '10' }, basis: { label: '实时估算', date: '2026-10-23', as_of: '2026-10-23T06:30:00Z' },
  quote: { value: '1.02', source: '合成估算源' }, equation: '累计收益 = 当前市值 − 剩余成本 + 已实现收益',
  price_notice: '估值差额已包含在累计收益中，今日变动不再重复相加。', fee_scope: '费用已包含在净到账中，不再从累计收益扣一次。',
  fee_notice: '买入费已计成本，管理费用已含净值。', recorded_sale_fees: '2', correction_count: 0,
  history_notice: '历史还原保留原标记，渠道实际校正优先。', estimate_results: { rows: [], resolved: 0 } });

test('unknown fund inputs render dashes rather than zero profits or NaN', () => {
  const value = base();
  value.components = Object.fromEntries(Object.keys(value.components).map(key => [key, null]));
  value.recorded_sale_fees = null; value.quote.value = null;
  const html = render(value);
  assert.match(html, /当前市值/); assert.match(html, /¥—/); assert.match(html, /剩余成本/);
  assert.doesNotMatch(html, /NaN|undefined|0\.00/);
});

test('estimates explain current mark once and never subtract recorded fees twice', () => {
  const html = render(base());
  assert.match(html, /实时估算/); assert.match(html, /合成估算源/); assert.match(html, /1\.0200/);
  assert.match(html, /今日变动不再重复相加/); assert.match(html, /不再从累计收益扣一次/);
  assert.match(html, /管理费用已含净值/);
});

test('history and corrections explain current book rather than another gain', () => {
  const value = base(); value.correction_count = 1;
  value.recent_events = [{ id: 'r', kind: 'REVERSAL', occurred_at: '2026-10-23T05:00:00Z', payload: { reason: '合成校正原因' } }];
  const html = render(value);
  assert.match(html, /1 次冲正/); assert.match(html, /合成校正原因/);
  assert.match(html, /不是另算一笔收益/); assert.match(html, /历史还原保留原标记/);
});

test('portfolio incompleteness remains unknown while known count and source stay explicit', () => {
  const html = render({ scope: 'PORTFOLIO', total_pnl: null, fund_pnl: null, other_tracked_pnl: null, mark_adjustment: null,
    equation: '总盈亏 = 各基金累计收益合计 + 其他跟踪项', known_count: 1, fund_count: 2, notice: '只提供已知小计。',
    funds: [{ fund_id: 'f', name: '未知基金', components: { pnl: null }, basis: { label: '估值待补', date: null } }] });
  assert.match(html, /已知 1 \/ 2 只/); assert.match(html, /估值待补/); assert.match(html, /不再相加/);
  assert.doesNotMatch(html, /0\.00|NaN|undefined/);
});

test('first formal receipt revisions and pending close stay separately labeled', () => {
  const value = base(); value.estimate_results = { resolved: 1, scored_count: 1, mae_pp: '.5', notice: '首次结果不覆盖。', rows: [
    { date: '2026-10-23', status: 'PENDING', estimate: { value: '1.02', source: '合成收盘源' }, formal_first: null },
    { date: '2026-10-22', status: 'RESOLVED', estimate: { value: '1.02' }, formal_first: { id: 'a', value: '1.025' },
      formal_latest: { id: 'b', value: '1.03' }, first_comparison: { change_error_pp: '-.5' } },
  ] };
  const html = render(value);
  assert.match(html, /等待披露/); assert.match(html, /修订 1\.0300/); assert.match(html, /1\.0250/);
  assert.match(html, /-0\.50 个百分点/); assert.match(html, /首次结果不覆盖/);
});
