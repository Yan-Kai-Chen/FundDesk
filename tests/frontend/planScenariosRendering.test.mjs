import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const front = new URL('../../src/frontend/', import.meta.url), requireFront = createRequire(new URL('package.json', front));
const ts = requireFront('typescript'), React = requireFront('react'), {renderToStaticMarkup} = requireFront('react-dom/server');
const compiled = new Map();
function componentURL(name) {
  if (compiled.has(name)) return compiled.get(name);
  const ext = existsSync(new URL(`src/${name}.tsx`, front)) ? 'tsx' : 'ts';
  const source = readFileSync(new URL(`src/${name}.${ext}`, front), 'utf8').replace(/^import ['"].*\.css['"];?\s*$/gm, '');
  let js = ts.transpileModule(source, {compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText;
  js = js.replace(/from (['"])([^'"]+)\1/g, (_, q, s) => `from ${q}${s.startsWith('./') ? componentURL(s.slice(2)) : pathToFileURL(requireFront.resolve(s)).href}${q}`);
  const url = `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`; compiled.set(name, url); return url;
}
const {PlanScenarios, PlanScenariosResult} = await import(componentURL('PlanScenarios'));
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));
const row = {period: 1, change: '-.012', action: 'BUY', amount: '200', fee: '0', t_units: '900',
  capital_used: '2200', t_available_units: '900', simulated: true, risk_state: '正常', date: '2026-10-21', trade_date: '2026-10-21',
  confirm_date: '2026-10-22', available_from: '2026-10-23', t_value: '889.20'};
const scenario = {id: 'CONTINUED_DECLINE', label: '连续下跌', rows: [row], ending_assets: '1950', period_pnl: '-250',
  external_contributions: '200', estimated_fees: '0', pending_buy: '0', virtual_receivable: '0', reason: '固定假设路径，不是收益预测或真实订单。'};
const result = () => ({initial_nav_date: '2026-10-20', initial_nav: '1', h: '.01',
  scenarios: [structuredClone(scenario), {...structuredClone(scenario), id: 'DECLINE_RECOVERY', label: '先跌后涨'},
    {...structuredClone(scenario), id: 'CONTINUED_RISE', label: '持续上涨'}],
  assumptions: ['原规则 T+1 确认、保守 T+2 可卖。', '不保证成交、回补或盈利。'], calendar_label: '日期只用于原规则计算。'});

test('plan stays folded and runs only by explicit button with no automatic budget', () => {
  const html = render(PlanScenarios, {fund: {id: 'f', units: '100', investment_plan_profile: {enabled: true}}, mode: 'real'});
  assert.match(html, /预演三种情景/); assert.match(html, /结果不保存、不下单/);
  assert.doesNotMatch(html, /<details[^>]*open|情景期末资产|¥4,000/);
  const disabled = render(PlanScenarios, {fund: {id: 'f', investment_plan_profile: {enabled: false}}, mode: 'real'});
  assert.match(disabled, /不会自动启用或填写总预算/); assert.doesNotMatch(disabled, /<button/);
});

test('fixed paths distinguish total assets, scenario contributions, fees, and assumption dates', () => {
  const html = render(PlanScenariosResult, {value: result()});
  for (const text of ['连续下跌', '先跌后涨', '持续上涨', '情景期末资产', '情景期间盈亏', '新增情景投入', '申请预计费用', '假设申请', '2026-10-21', '2026-10-22', '2026-10-23']) assert.match(html, new RegExp(text));
  assert.match(html, /不是今日实况/); assert.match(html, /应收不代表已到账现金/);
  assert.match(html, /可卖 900\.00/);
  assert.match(html, /-250\.00/); assert.doesNotMatch(html, /NaN|稳赢|最优收益/);
});

test('in-flight uncertainty leaves complete totals empty and labels known subset', () => {
  const value = result(); Object.assign(value.scenarios[0], {ending_assets: null, period_pnl: null,
    existing_in_flight_count: 2, known_scope_assets: '1950', known_scope_period_pnl: '-250'});
  const html = render(PlanScenariosResult, {value});
  assert.match(html, /2 笔在途不模拟确认/); assert.match(html, /完整资产与损益留空/);
  assert.match(html, /已确认及新增情景范围/); assert.match(html, /<strong>—<\/strong>/);
});

test('unknown calendar uses numbered periods and never fabricates an application', () => {
  const value = result(); value.scenarios[0].rows = [{period: 1, change: '-.012', amount: null, fee: null,
    t_units: null, capital_used: null, date: null, reason: '后续参考日历未核实，金额停止计算。'}];
  const html = render(PlanScenariosResult, {value});
  assert.match(html, /第 1 期/); assert.match(html, /后续参考日历未核实/); assert.match(html, /未核实/);
  assert.doesNotMatch(html, /假设申请|NaN/);
});
