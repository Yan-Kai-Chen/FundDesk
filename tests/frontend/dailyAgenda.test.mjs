import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const front = new URL('../../src/frontend/', import.meta.url);
const requireFront = createRequire(new URL('package.json', front));
const ts = requireFront('typescript'), React = requireFront('react');
const {renderToStaticMarkup} = requireFront('react-dom/server');
const source = readFileSync(new URL('src/DailyAgenda.tsx', front), 'utf8').replace(/^import ['"].*\.css['"];?\s*$/gm, '');
let js = ts.transpileModule(source, {compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext,
  target: ts.ScriptTarget.ES2022}}).outputText;
js = js.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => `from ${quote}${specifier.startsWith('./') ?
  new URL(`src/${specifier.slice(2)}.ts`, front).href : pathToFileURL(requireFront.resolve(specifier)).href}${quote}`);
const {DailyAgenda,DailyAgendaContent} = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const row = (id='f') => ({id:`fund:${id}`,fund_id:id,fund_name:'测试基金',action:'SELL',actionable:true,
  amount:'200.00',units:'123.45',fee:'0.00',net:'200.00',expected_realized_pnl:'3.10',reason:'已核对成本与费用',
  prefill:{fund_id:id,side:'SELL',bucket:'TACTICAL',requested:'123.45'},basis:{value_kind:'THIRD_PARTY_ESTIMATE',effective_date:'2026-10-23'}});
const value = () => ({date:'2026-10-23',summary:{label:'1 只基金有操作参考'},actions:[row()],needs_input:[],waiting:[],funds:[row()]});
const props = data => ({value:data,onSelectFund(){},onOrder(){},onEditOrder(){}});
const render = data => renderToStaticMarkup(React.createElement(DailyAgenda,props(data)));

test('agenda shows net sale profit and fees with a recording-only button',()=>{
  const html=render(value());
  assert.match(html,/今日待办/);assert.match(html,/预计净实现收益/);assert.match(html,/\+3\.10/);assert.match(html,/预计费用/);
  assert.match(html,/预填交易记录/);assert.match(html,/在渠道完成实际买卖后保存/);
  assert.doesNotMatch(html,/自动下单/);
});

test('normal waiting is folded and never styled as missing information',()=>{
  const data=value();data.actions=[];data.funds=[{...row(),actionable:false,label:'今日申请已截止',reason:'15:00 后仅供查看'}];
  data.waiting=[{id:'order:o',order_id:'o',fund_id:'f',fund_name:'测试基金',side:'BUY',requested:'200',label:'待自动入账',reason:'预计下个参考日入仓',needs_input:false}];
  const html=render(data);
  assert.match(html,/<details class="agenda-waiting">/);assert.match(html,/无需重复确认/);assert.match(html,/为何暂不操作/);
  assert.doesNotMatch(html,/agenda-input|预填交易记录/);
});

test('buy reference does not promise a profit and missing fees remain unknown',()=>{
  const data=value();data.actions=[{...row(),action:'BUY',expected_realized_pnl:null,fee:null}];
  const html=render(data);
  assert.match(html,/买入后尚无已实现收益/);assert.match(html,/预计费用 待核对/);
  assert.doesNotMatch(html,/预计净实现收益|\+0\.00/);
});

test('collapsed extra actions keep the default overview short',()=>{
  const data=value();data.actions=Array.from({length:5},(_,i)=>row(String(i)));
  const html=render(data);
  assert.match(html,/<details class="agenda-more"><summary>其余 2 只操作参考/);
});

test('callbacks select a fund or prepare the exact existing form without saving',()=>{
  const data=value();const calls=[];
  data.needs_input=[{id:'order:o',order_id:'order-id',fund_id:'f',fund_name:'测试基金',side:'BUY',requested:'200',label:'待扣款记录',needs_input:true}];
  const tree=DailyAgendaContent({...props(data),onOrder:target=>calls.push(['order',target]),onSelectFund:id=>calls.push(['fund',id]),onEditOrder:id=>calls.push(['edit',id])});
  const buttons=[];
  function walk(element){if(Array.isArray(element)){element.forEach(walk);return;}if(!element||typeof element!=='object')return;if(element.type==='button')buttons.push(element);walk(element.props?.children);}
  walk(tree);
  buttons.find(button=>button.props.className.includes('agenda-record')).props.onClick();
  buttons.find(button=>button.props.className==='agenda-fund-name').props.onClick();
  buttons.find(button=>button.props.className==='text-button').props.onClick();
  assert.deepEqual(calls,[['order',data.actions[0].prefill],['fund','f'],['edit','order-id']]);
});

test('expired or malformed local expiry hides the form button even without a network refresh',()=>{
  const data=value();data.actions[0].expires_at='2026-10-23T07:00:00Z';data.funds=data.actions;
  let html=renderToStaticMarkup(React.createElement(DailyAgendaContent,{...props(data),now:Date.parse('2026-10-23T07:00:00Z')}));
  assert.doesNotMatch(html,/预填交易记录|1 只基金有操作参考/);assert.match(html,/参考已过期/);
  data.actions[0].expires_at='invalid';
  html=renderToStaticMarkup(React.createElement(DailyAgendaContent,{...props(data),now:0}));
  assert.doesNotMatch(html,/预填交易记录/);
});
