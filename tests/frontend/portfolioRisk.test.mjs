import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const front=new URL('../../src/frontend/',import.meta.url), requireFront=createRequire(new URL('package.json',front));
const ts=requireFront('typescript'), React=requireFront('react'), {renderToStaticMarkup}=requireFront('react-dom/server');
const compiled=new Map();
function componentURL(name){
  if(compiled.has(name))return compiled.get(name);
  const ext=existsSync(new URL(`src/${name}.tsx`,front))?'tsx':'ts';
  const source=readFileSync(new URL(`src/${name}.${ext}`,front),'utf8').replace(/^import ['"].*\.css['"];?\s*$/gm,'');
  let js=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
  js=js.replace(/from (['"])([^'"]+)\1/g,(_,q,s)=>`from ${q}${s.startsWith('./')?componentURL(s.slice(2)):pathToFileURL(requireFront.resolve(s)).href}${q}`);
  const url=`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;compiled.set(name,url);return url;
}
const {PortfolioRiskContent,portfolioRiskMatchesSnapshot}=await import(componentURL('PortfolioRisk'));
const render=(value,now=Date.parse('2026-10-23T06:00:00Z'))=>renderToStaticMarkup(React.createElement(PortfolioRiskContent,{value,now,onSelectFund:()=>{}}));
function value(){return {allocation:{status:'READY',holding_count:2,total_value:'400',known_subtotal:'400',missing_fund_ids:[],
  largest_group:{name:'我的科技',weight:'.75'},groups:[{group_id:'g',name:'我的科技',value:'300',weight:'.75',fund_ids:['a']}],
  funds:[{fund_id:'a',name:'基金甲'}]},notice:'仅含 App 已确认基金；分类不是基金真实资产穿透。',
  daily_operations:{action_count:2,buy_count:1,sell_count:1,buy_amount:'200',sell_gross:'110',sell_net:'109',estimated_fees:'1',net_subscription:'91',expires_at:'2026-10-23T06:15:00Z'},
  correlations:{ready_count:1,minimum_samples:20,history:{a:{label:'总回报历史'},b:{label:'总回报历史'}},notice:'至少20期；相关性不代表实际重仓重叠。',
    pairs:[{fund_ids:['a','b'],names:['基金甲','基金乙'],status:'READY',sample_count:60,start:'2026-07-01',end:'2026-10-22',correlation:'.91',label:'高度同向'}]}};}

test('risk panel is collapsed by default and separates category proportion from return correlation',()=>{
  const html=render(value());
  assert.match(html,/<details class="portfolio-risk panel">/);assert.doesNotMatch(html,/<details[^>]*portfolio-risk[^>]*open/);
  assert.match(html,/我的科技 75\.0%/);assert.match(html,/width:75%/);assert.match(html,/0\.91/);
  assert.match(html,/60 个相同区间/);assert.match(html,/不是基金真实资产穿透/);assert.match(html,/不作风险评级/);
  assert.doesNotMatch(html,/NaN|保证盈利|持仓穿透率/);
});

test('partial holdings show known subtotal and suppress complete amount and allocation bar',()=>{
  const v=value();v.allocation={...v.allocation,status:'PARTIAL',total_value:null,known_subtotal:'300',largest_group:null,missing_fund_ids:['b'],
    groups:[{group_id:'g',name:'自定组',value:null,known_subtotal:'300',weight:null,fund_ids:['a','b']}]};
  const html=render(v);
  assert.match(html,/已知市值 ¥300\.00/);assert.match(html,/比例待补/);assert.match(html,/整体总额与占比暂不计算/);
  assert.doesNotMatch(html,/width:|100\.0%|App总持仓/);
});

test('current operation aggregate states its timing and cash boundary',()=>{
  const html=render(value());
  assert.match(html,/参考买入 · 1 只/);assert.match(html,/¥200\.00/);assert.match(html,/预计卖出净到账/);
  assert.match(html,/¥109\.00/);assert.match(html,/未假设回款可立即复用/);assert.match(html,/不会自动下单/);
});

test('expired operation total is removed without a background refresh or zero estimate',()=>{
  const html=render(value(),Date.parse('2026-10-23T06:15:00Z'));
  assert.match(html,/参考已过期/);assert.doesNotMatch(html,/¥200\.00|¥109\.00|参考买入 · 1 只/);
  const v=value();v.daily_operations={action_count:0};const idle=render(v);
  assert.match(idle,/当前没有可执行参考/);assert.match(idle,/午休、截止及过期参考不计入/);
});

test('incomplete action history has no fabricated correlation coefficient',()=>{
  const v=value();v.correlations.ready_count=0;v.correlations.history.a.label='分红与拆分资料待核对';
  v.correlations.pairs[0]={...v.correlations.pairs[0],status:'HISTORY_REQUIRED',correlation:null,label:'历史资料待核对'};
  const html=render(v);
  assert.match(html,/暂没有满足至少 20 个相同收益区间/);assert.match(html,/分红与拆分资料待核对/);
  assert.doesNotMatch(html,/0\.91|高度同向<\/span>/);
});

function matching(){const state={business_date:'2026-10-23',revision:1,fund_sidebar:{_version:3},funds:[{id:'a',units:'100',rules:{_version:1},
  daily_t:{profile:{_version:2},today:{quote:{id:'q'},baseline:{id:'base'}},market:{date:'2026-10-23'}},investment_plan_profile:{_version:4},
  nav:{id:'formal'},return_valuation:{quote_id:'q',holding_value:'300'}}]};
  const risk={date:state.business_date,revision:1,sidebar_version:3,snapshot_basis:[{fund_id:'a',units:'100',rule_version:1,t_profile_version:2,
    investment_plan_version:4,quote_id:'q',baseline_id:'base',market_date:state.business_date,nav_id:'formal',valuation_quote_id:'q',holding_value:'300'}]};
  return [state,risk];}
test('same ledger revision cannot mix newly priced holdings or edited classification',()=>{
  const [state,risk]=matching();assert.equal(portfolioRiskMatchesSnapshot(state,risk),true);
  for(const change of ['quote','amount','nav','sidebar','units','profile']){
    const copy=structuredClone(state),fund=copy.funds[0];
    if(change==='quote')fund.return_valuation.quote_id='new';
    else if(change==='amount')fund.return_valuation.holding_value='301';
    else if(change==='nav')fund.nav.id='new-formal';
    else if(change==='sidebar')copy.fund_sidebar._version=4;
    else if(change==='units')fund.units='101';
    else fund.investment_plan_profile._version=5;
    assert.equal(portfolioRiskMatchesSnapshot(copy,risk),false,change);
  }
  assert.equal(portfolioRiskMatchesSnapshot(state,null),false);
});
