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
const {TReviewPanel}=await import(componentURL('TReviewPanel'));
const render=review=>renderToStaticMarkup(React.createElement(TReviewPanel,{fund:{id:'f',t_review:review},mode:'real',revision:4,onChanged:()=>{}}));
const base=()=>({exit_reference:{count:0,difference:'0',status:'READY',rows:[],quote:{}},
  scenario:{status:'NOT_STARTED',difference:null},notice:'情景假设；虚拟余额不是实际钱包现金。'});

test('no sales shows a usable zero reference without claiming realized alpha',()=>{
  const html=render(base());
  assert.match(html,/暂无做 T 卖出/);assert.match(html,/退出参照为 0/);
  assert.match(html,/资金复用情景/);assert.match(html,/假设对照/);assert.match(html,/从当前建立基准/);
  assert.doesNotMatch(html,/已证实|超额收益|真实增益|NaN/);
});

test('scenario money is expressly virtual and same contribution comparator is visible',()=>{
  const review=base();review.baseline={_version:1,units:'100',nav:'1',started_at:'2026-10-23T06:00:00Z'};
  review.scenario={status:'READY_SCENARIO',difference:'4.4',actual_value:'114.4',hold_value:'110',virtual_cash:'0',receivable:'0',external_contributions:'0'};
  const html=render(review);
  assert.match(html,/\+4\.40/);assert.match(html,/操作情景资产/);assert.match(html,/不卖出持有参照/);
  assert.match(html,/先使用虚拟余额/);assert.match(html,/不表示实际钱包余额/);
  assert.match(html,/从当前重新建立基准/);
});

test('incomplete contribution scope never becomes a precise profit result',()=>{
  const review=base();review.scenario={status:'PARTIAL',difference:null,reasons:[{code:'BASE_SALE',message:'期间有底仓卖出，请重新建立基准。'}]};
  const html=render(review);
  assert.match(html,/暂不输出精确增益/);assert.match(html,/期间有底仓卖出/);
  assert.doesNotMatch(html,/操作情景相对不卖出持有的差额|操作情景资产/);
});

test('existing in-transit trades wait for automatic freeze without showing zero gain',()=>{
  const review=base();review.baseline={_version:1,start_pending:true,requested_at:'2026-10-23T06:00:00Z'};
  review.scenario={status:'WAIT_BASELINE',difference:null,reasons:[{code:'WAIT_EXISTING_ORDERS',message:'期间新增订单也等待完成。'}]};
  const html=render(review);
  assert.match(html,/待已有在途完成后自动建立参照/);assert.match(html,/比较从实际自动建立时开始/);
  assert.match(html,/等待自动建立/);assert.doesNotMatch(html,/操作情景相对不卖出持有的差额|操作情景资产/);
});
