import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const front=new URL('../../src/frontend/',import.meta.url);
const requireFront=createRequire(new URL('package.json',front));
const ts=requireFront('typescript'),React=requireFront('react');
const {renderToStaticMarkup}=requireFront('react-dom/server');
const compiled=new Map();
function componentURL(name){
  if(compiled.has(name))return compiled.get(name);
  const source=readFileSync(new URL(`src/${name}.tsx`,front),'utf8');
  let js=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
  js=js.replace(/from (['"])([^'"]+)\1/g,(_,q,s)=>`from ${q}${s==='./SellImpact'?componentURL('SellImpact'):s.startsWith('./')?new URL(`src/${s.slice(2)}.ts`,front).href:pathToFileURL(requireFront.resolve(s)).href}${q}`);
  const url=`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;compiled.set(name,url);return url;
}
const {SellImpact}=await import(componentURL('SellImpact'));
const {Forms}=await import(componentURL('Forms'));

function value(){
 return {bucket:'TACTICAL',units:'600',nav:'.88276',fee:'2.50',net:'527.16',removed_cost:'532.84',holding_pnl_before_fee:'-3.18',expected_realized_pnl:'-5.68',fallback_units:'582.80',fallback_principal:'517.57',t_units:'17.20',realized_before:'29.94',realized_after:'24.26',sell_effect:'-2.49',status:'READY',day_change:'.066135',rounding_effect:'.01',
  basis:{effective_date:'2026-10-23',value_kind:'THIRD_PARTY_ESTIMATE'},
  before:{total_pnl:'42.88',groups:{TACTICAL:{unrealized:'-.09'}}},after:{total_pnl:'40.39',groups:{TACTICAL:{unrealized:'0'}}},
  buy_comparison:{status:'READY',bucket:'BASE',order_id:'buy',submitted_at:'2026-10-22T06:00:00Z',remaining_cost:'300',remaining_units:'362.32',covered_units:'362.32',uncovered_units:'237.68',gross_price_edge:'19.84',net_price_edge:'18.33',fee_share:'1.51',remaining_holding_pnl:'19.84',available_units:'0',options:[{order_id:'buy',bucket:'BASE',submitted_at:'2026-10-22T06:00:00Z',remaining_units:'362.32'}]}};
}
const render=v=>renderToStaticMarkup(React.createElement(SellImpact,{value:v,onReferenceChange(){},onEditBuy(){}}));

test('positive purchase-price comparison and negative booked loss remain separate',()=>{
 const html=render(value());
 assert.match(html,/买入价差参考 · 底仓/);assert.match(html,/\+18\.33/);
 assert.match(html,/按原成本实现损益/);assert.match(html,/-5\.68/);
 assert.match(html,/另外 237\.68 份未纳入价差参考/);assert.match(html,/不计入累计收益/);
 assert.match(html,/较昨日 \+6\.61%/);assert.match(html,/持有浮盈亏 \+19\.84/);
 assert.match(html,/查看 \/ 修改这笔买入归属/);
});

test('base fallback is prominent and newly bought unavailable units are not claimed sellable',()=>{
 const html=render(value());
 assert.match(html,/本次含底仓卖出/);assert.match(html,/T 仓 17\.20 份 \+ 底仓 582\.80 份/);
 assert.match(html,/可赎回 0\.00 份/);assert.match(html,/不代表这些新买份额已可卖/);
 assert.match(html,/\+42\.88 → \+40\.39/);assert.match(html,/影响 -2\.49 元/);
});

test('no confirmed comparison does not invent a T profit',()=>{
 const v=value();v.buy_comparison={status:'NO_REMAINING_BUY',notice:'暂无可核对的剩余买入订单',options:[]};
 const html=render(v);
 assert.match(html,/暂无可核对的剩余买入订单/);assert.match(html,/按原成本实现损益/);
 assert.doesNotMatch(html,/本轮 T 价差参考|买入价差参考 · 底仓/);
});

test('tactical purchase uses a reference label without editing the base purchase automatically',()=>{
 const v=value();v.buy_comparison.bucket='TACTICAL';
 const html=render(v);
 assert.match(html,/本轮 T 价差参考/);assert.doesNotMatch(html,/修改这笔买入归属/);
});

function form(target={},resume=false){
 const f={id:'f',name:'合成基金',lots:[],investment_plan_profile:{enabled:true},rules:{},strategy:{}};
 return renderToStaticMarkup(React.createElement(Forms,{dialog:{type:'order',fundId:'f',target,resume},state:{now:'2026-10-23T06:00:00Z',funds:[f],orders:[],revision:0},mode:'real',onClose(){},onOpen(){},onSuccess:async()=>{},toast(){}}));
}

test('new plan trades default to T while explicit base intent is preserved',()=>{
 assert.match(form(),/<option value="TACTICAL" selected="">做 T<\/option>/);
 assert.match(form({bucket:'BASE'}),/<option value="BASE" selected="">底仓<\/option>/);
});
