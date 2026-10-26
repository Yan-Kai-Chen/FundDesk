import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const front=new URL('../../src/frontend/',import.meta.url),requireFront=createRequire(new URL('package.json',front));
const ts=requireFront('typescript'),React=requireFront('react'),{renderToStaticMarkup}=requireFront('react-dom/server');
const compiled=new Map();
function componentURL(name){
  if(compiled.has(name))return compiled.get(name);
  const ext=existsSync(new URL(`src/${name}.tsx`,front))?'tsx':'ts';
  const source=readFileSync(new URL(`src/${name}.${ext}`,front),'utf8').replace(/^import ['"].*\.css['"];?\s*$/gm,'');
  let js=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
  js=js.replace(/from (['"])([^'"]+)\1/g,(_,q,s)=>`from ${q}${s.startsWith('./')?componentURL(s.slice(2)):pathToFileURL(requireFront.resolve(s)).href}${q}`);
  const url=`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;compiled.set(name,url);return url;
}
const {ReconciliationPanel,ReconciliationResult}=await import(componentURL('ReconciliationPanel'));
const render=(component,props)=>renderToStaticMarkup(React.createElement(component,props));
function result(){return {fund_id:'f',status:'DIFFERENCE',difference_count:1,input:{snapshot_at:'2026-10-23T06:00:00Z',source:'支付宝'},
  comparisons:[{field:'confirmed_units',label:'已确认份额',reported:'100.13',local:'100.12',difference:'.01',tolerance:'.005',status:'DIFFERENCE'}],
  unassessed:[{field:'reported_fee',reason:'费用须对应原订单或批次，此处只保留资料；请到原交易校正'}],
  baseline:{pending_order_count:1,nav_date:'2026-10-22',nav_value:'1.20'}};}

test('only differences are prominent and link back to the original ledger',()=>{
  const html=render(ReconciliationResult,{result:result(),onOpenOrders:()=>{}});
  assert.match(html,/存在差异/);assert.match(html,/平台 100\.13/);assert.match(html,/本地 100\.12/);assert.match(html,/差 \+0\.01/);
  assert.match(html,/查看原交易/);assert.match(html,/1 笔待确认申请，未计入已确认份额/);assert.match(html,/只保留资料/);
  assert.doesNotMatch(html,/自动补差|自动更正|生成补差交易/);
});

test('a stale result preserves frozen evidence and identifies its time',()=>{
  const r={...result(),status:'STALE',stale:true,stale_notice:'本基金持仓已变化，请用新截图核对。'};
  const html=render(ReconciliationResult,{result:r});
  assert.match(html,/需新截图核对/);assert.match(html,/保存时/);assert.match(html,/截图时间/);assert.match(html,/10\/23/);
  assert.match(html,/本地 100\.12/);assert.doesNotMatch(html,/份额已核对/);
});

test('matched fund remains collapsed with no attention badge or confirmation step',()=>{
  const r={...result(),status:'MATCHED',difference_count:0,comparisons:result().comparisons.map(x=>({...x,status:'MATCHED',difference:'0'}))};
  const html=render(ReconciliationPanel,{fund:{id:'f',reconciliation:{latest:r}},mode:'real'});
  assert.match(html,/<details class="panel reconciliation-panel"><summary><strong>持仓核对<\/strong><\/summary>/);
  assert.match(html,/自动入仓无需逐笔确认/);assert.match(html,/份额一致/);assert.doesNotMatch(html,/1 项差异|确认到账/);
});

test('unreconciled fund offers snapshot input without pretending it has a difference',()=>{
  const html=render(ReconciliationPanel,{fund:{id:'f'},mode:'real'});
  assert.match(html,/填写平台持仓/);assert.doesNotMatch(html,/存在差异|份额已核对/);
});
