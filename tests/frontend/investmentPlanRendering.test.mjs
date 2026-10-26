import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const front = new URL('../../src/frontend/', import.meta.url);
const requireFront = createRequire(new URL('package.json', front));
const ts = requireFront('typescript');
const React = requireFront('react');
const { renderToStaticMarkup } = requireFront('react-dom/server');
const compiled = new Map();

// Render the real component without a browser or a second UI runtime. Only
// import paths and the CSS side effect change; view/recording logic stays real.
function componentURL(name) {
  if (compiled.has(name)) return compiled.get(name);
  const source = readFileSync(new URL(`src/${name}.tsx`, front), 'utf8').replace(/^import ['"].*\.css['"];?\s*$/gm, '');
  let js = ts.transpileModule(source, { compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022,
  }}).outputText;
  js = js.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => {
    const target = specifier === './SellImpact' ? componentURL('SellImpact') : specifier.startsWith('./') ?
      new URL(`src/${specifier.slice(2)}.ts`, front).href : pathToFileURL(requireFront.resolve(specifier)).href;
    return `from ${quote}${target}${quote}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;
  compiled.set(name, url); return url;
}

const { InvestmentPlanReference, InvestmentPlanForm } = await import(componentURL('InvestmentPlan'));
const render = (fund, reference) => renderToStaticMarkup(React.createElement(InvestmentPlanReference,
  {fund, reference, onSettings() {}, onOrder() {}}));

function current() {
  const history = [{id:'nav',value_kind:'OFFICIAL_NAV',effective_date:'2026-10-22',observed_at:'2026-10-22T12:00:00Z'}];
  const risk = {ready:true,state:'PAUSED',label:'暂停加仓',h:'.026',streak:1,return_5:'-.08',drawdown_20:'-.31',
    reasons:['20日高点回撤达到暂停线'],rolling_buy_used:'300',rolling_buy_limit:'450',rolling_buy_remaining:'150',
    cost_basis:'4400',capital_used:'4400',capital_headroom:'1600',recovery_conditions:['连续两个正式上涨日再核对恢复条件'],
    input_ids:['nav'],input_basis:{history_count:1,history_end_date:'2026-10-22',date:'2026-10-22',quote_id:'quote'}};
  const fund = {id:'ct',name:'财通',history,investment_plan_profile:{enabled:true,holding_cap:'6000',_version:4,template:'DRAWDOWN_GUARD_V2'},
    daily_t:{market:{date:'2026-10-23'},today:{status:'READY',quote:{id:'quote'}}}};
  const reference = {fund_id:'ct',level:'PLAN',profile_version:4,can_apply:false,action:'HOLD',amount:'0',label:'暂停加仓',
    reason:'等待止跌恢复',plan:{risk,template:'DRAWDOWN_GUARD_V2',raw_target_today:'300',target_today:'0',used_buy:'0',used_sell:'0',
      remaining_daily:'900',committed_holding:'4400',headroom:'1600',tiers:[{trigger:'.026',amount:'300'},{trigger:'.052',amount:'600'},{trigger:'.078',amount:'900'}]}};
  return {fund,reference};
}

test('not-open quote and known paused historical risk are shown together without an invented amount',()=>{
  const {fund,reference}=current();
  fund.daily_t.today={status:'NOT_OPEN',quote:null};
  Object.assign(reference,{action:'WAIT',label:'没开盘',amount:null});
  reference.plan.risk.input_basis.quote_id=null;
  const html=render(fund,reference);
  assert.match(html,/没开盘/);assert.match(html,/暂停加仓/);assert.match(html,/31\.00%/);
  assert.match(html,/等待有效报价或条件/);assert.match(html,/等待资料/);
  assert.doesNotMatch(html,/参考买入 ¥0|<button[^>]*>记录已操作/);
});

test('unknown risk threshold remains missing instead of showing a valid zero-percent tier',()=>{
  const {fund,reference}=current();
  Object.assign(reference,{action:'WAIT',label:'资料待补',amount:null});
  Object.assign(reference.plan.risk,{ready:false,h:null,return_5:null,drawdown_20:null,input_ids:[],
    input_basis:{history_count:0,history_end_date:null,history_digest:null}});
  const html=render(fund,reference);
  assert.match(html,/历史资料不足，触发幅度待计算/);
  assert.doesNotMatch(html,/0\.00%|不足 —|<button[^>]*>记录已操作/);
});

test('paused additions do not hide a backend-approved tactical sale',()=>{
  const {fund,reference}=current();
  Object.assign(reference,{action:'SELL',label:'参考卖出',can_apply:true,amount:'299.98',units:'94.63',fee:'0'});
  const html=render(fund,reference);
  assert.match(html,/暂停加仓/);assert.match(html,/参考卖出 ¥299\.98/);assert.match(html,/<button[^>]*>记录已操作/);
});

test('editing a guarded plan preserves the selected template and existing cap',()=>{
  const {fund}=current();
  const html=renderToStaticMarkup(React.createElement(InvestmentPlanForm,{fund,mode:'real',onClose(){},onSaved:async()=>{}}));
  assert.match(html,/<option value="DRAWDOWN_GUARD_V2" selected="">多日防御<\/option>/);
  assert.match(html,/value="6000"/);assert.match(html,/价格下跌不会自动释放已投入本金/);
});
