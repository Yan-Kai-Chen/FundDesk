import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const front = new URL('../../src/frontend/', import.meta.url);
const requireFront = createRequire(new URL('package.json', front));
const ts = requireFront('typescript'), React = requireFront('react');
const { renderToStaticMarkup } = requireFront('react-dom/server');
const compiled = new Map();
function componentURL(name) {
  if (compiled.has(name)) return compiled.get(name);
  const extension = existsSync(new URL(`src/${name}.tsx`, front)) ? 'tsx' : 'ts';
  const source = readFileSync(new URL(`src/${name}.${extension}`, front), 'utf8').replace(/^import ['"].*\.css['"];?\s*$/gm, '');
  let js = ts.transpileModule(source, {compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022,
  }}).outputText;
  js = js.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => {
    const local = specifier.slice(2);
    const target = specifier.startsWith('./') ? componentURL(local) : pathToFileURL(requireFront.resolve(specifier)).href;
    return `from ${quote}${target}${quote}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;
  compiled.set(name, url); return url;
}
const { PositionBuckets } = await import(componentURL('PositionBuckets'));
const { TrackingSummary, FundHoldings } = await import(componentURL('TrackingDesk'));
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));

function fixture() {
  const groups = {BASE: {units:'600',value:'600',cost:'650',unrealized:'-50',realized:'-5',pnl:'-55',reserved:'0'},
    TACTICAL: {units:'400',value:'400',cost:'420',unrealized:'-20',realized:'25',pnl:'5',reserved:'0'}};
  const current = {BASE: {...groups.BASE,value:'612',unrealized:'-38',pnl:'-43'},
    TACTICAL: {...groups.TACTICAL,value:'408',unrealized:'-12',pnl:'13'}};
  return {id:'f',position_buckets:{status:'CONFIRMED',groups,total_pnl:'-50',sales:[],positions:[]},
    holding_composition:{status:'READY',total:{value:'1020',units:'1000',unrealized:'-50',pnl:'-30',holding_return:'-.046728971963'},pnl_status:'READY',groups:{
      BASE:{...current.BASE,weight:'.6',pnl_weight:'.767857142857',pnl_direction:'LOSS',holding_return:'-.058461538462'},
      TACTICAL:{...current.TACTICAL,weight:'.4',pnl_weight:'.232142857143',pnl_direction:'GAIN',holding_return:'-.028571428571'}}},
    day_valuation:{status:'READY',price_change:'20',is_estimate:true},
    return_valuation:{label:'收盘估算',date:'2026-10-23',as_of:'2026-10-23T07:00:00Z',is_estimate:true,
      official_total_pnl:'-50',summary:{groups:current,total_pnl:'-30'}}};
}

test('income panel shows current daily move, current cumulative return, and fixed realized T together',()=>{
  const html=render(PositionBuckets,{fund:fixture(),mode:'real',tz:'Asia/Tokyo',onSaved:async()=>{}});
  assert.match(html,/收盘估算 · 10\/23 · 15:00/);
  assert.match(html,/今日变动参考/);assert.match(html,/\+20\.00/);
  assert.match(html,/累计收益<\/span><strong>-30\.00/);
  assert.match(html,/做 T 已实现<\/span><strong>\+25\.00/);
  assert.match(html,/T 仓持有浮动 -12\.00 元/);
  assert.match(html,/最近正式累计收益：-50\.00 元/);
  assert.match(html,/不再叠加该变动/);
});

test('formal-only fallback is explicit and missing daily amount stays unknown',()=>{
  const fund=fixture();
  Object.assign(fund.return_valuation,{label:'最近正式',date:'2026-10-22',is_estimate:false,
    summary:{groups:fund.position_buckets.groups,total_pnl:'-50'}});
  fund.day_valuation={status:'NOT_OPEN',price_change:null};
  const html=render(PositionBuckets,{fund,mode:'real',tz:'Asia/Tokyo',onSaved:async()=>{}});
  assert.match(html,/最近正式 · 10\/22/);assert.match(html,/没开盘/);
  assert.match(html,/累计收益<\/span><strong>-50\.00/);
  assert.doesNotMatch(html,/今日变动参考<\/span><strong>0\.00/);
});

test('historical classification preview is not overwritten with confirmed live positions',()=>{
  const fund=fixture();
  fund.statement={bucket_analysis:{...fund.position_buckets,status:'REFERENCE',buys:[],sells:[],assignments:[]}};
  const html=render(PositionBuckets,{fund,mode:'real',tz:'Asia/Tokyo',onSaved:async()=>{}});
  assert.match(html,/累计收益<\/span><strong>-50\.00/);
  assert.doesNotMatch(html,/收盘估算|今日变动参考/);
});

test('current fund holdings and portfolio headline use the same marked basis',()=>{
  const html=render(FundHoldings,{fund:fixture()});
  assert.match(html,/¥ 612\.00/);assert.match(html,/¥ 408\.00/);
  assert.match(html,/-12\.00<\/strong>/);assert.match(html,/60\.00%/);assert.match(html,/40\.00%/);
  assert.match(html,/1,020\.00/);assert.match(html,/is-loss/);assert.match(html,/按绝对值/);
  const portfolio={market_value:'2000',pnl:'-100',bucket_values:{BASE:'1200',TACTICAL:'800'},
    return_valuation:{label:'含当日估算',current_count:1,last_official_count:1,market_value:'2020',pnl:'-80',
      bucket_values:{BASE:'1212',TACTICAL:'808'}}};
  const top=render(TrackingSummary,{portfolio});
  assert.match(top,/2,020\.00/);assert.match(top,/-80\.00/);
  assert.match(top,/1 只沿用最近正式值/);assert.doesNotMatch(top,/-100\.00/);
});

test('small tactical slice is proportional and retains readable amount and profit outside the graphic',()=>{
  const fund=fixture();
  fund.holding_composition.groups.BASE.weight='.991';fund.holding_composition.groups.TACTICAL.weight='.009';
  const html=render(FundHoldings,{fund});
  assert.match(html,/99\.10%/);assert.match(html,/0\.90%/);
  assert.match(html,/stroke-dasharray="0\.8999999999999999 99\.1"|stroke-dasharray="0\.9 99\.1"/);
  assert.match(html,/¥ 408\.00/);assert.match(html,/\+13\.00/);
});

test('unknown allocation does not pretend to be a full base position or a zero-profit account',()=>{
  const fund=fixture();delete fund.holding_composition;
  const html=render(FundHoldings,{fund});
  assert.match(html,/持仓待填写|待补资料/);assert.match(html,/盈亏资料待补/);
  assert.doesNotMatch(html,/NaN|Infinity|100\.00%|¥ 0\.00/);
});
