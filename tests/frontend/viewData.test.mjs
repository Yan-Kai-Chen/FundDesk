import test from 'node:test';
import assert from 'node:assert/strict';
import { currentDayDisplay,shareData,shareSnapshot,displayPlan,planMatchesSnapshot,investmentPlanRecord,investmentPlanMatchesFund } from '../../src/frontend/src/viewData.ts';

test('all daily quote displays use current estimate or official selection instead of old NAV move',()=>{
  const fund={nav:{value:'1',effective_date:'2026-10-20'},change:'0.04',daily_t:{market:{date:'2026-10-21'},
    today:{status:'READY',phase:'LIVE',change:'-0.01',quote:{value_kind:'THIRD_PARTY_ESTIMATE',value:'0.99',as_of:'2026-10-21T13:00:00+08:00'}}}};
  assert.equal(currentDayDisplay(fund).change,'-0.01');
  assert.equal(currentDayDisplay(fund).label,'盘中估算');
  fund.daily_t.today.phase='CLOSE';
  assert.equal(currentDayDisplay(fund).label,'收盘估算');
  fund.daily_t.today.quote={value_kind:'OFFICIAL_NAV',value:'0.98'};
  fund.daily_t.today.change='-0.02';fund.daily_t.today.phase='OFFICIAL';
  assert.equal(currentDayDisplay(fund).value,'0.98');
  assert.equal(currentDayDisplay(fund).label,'当日正式');
  assert.equal(currentDayDisplay(fund).official,true);
});
test('missing or stale today never presents yesterday as a live daily quote',()=>{
  for (const status of ['MISSING','STALE','NOT_OPEN']) {
    const d=currentDayDisplay({nav:{value:'1'},change:'.04',daily_t:{market:{date:'2026-10-22'},today:{status,label:'没开盘'}}});
    assert.equal(d.value,null);assert.equal(d.change,null);assert.equal(d.label,'没开盘');
  }
  assert.equal(currentDayDisplay().ready,false);
});
test('lunch displays the backend-selected morning reference and its original timestamp',()=>{
  const d=currentDayDisplay({daily_t:{market:{date:'2026-10-20'},today:{status:'READY',phase:'BREAK',change:'.025',
    quote:{value_kind:'THIRD_PARTY_ESTIMATE',value:'1.23',as_of:'2026-10-20T11:29:00+08:00'}}}});
  assert.equal(d.ready,true); assert.equal(d.label,'午间休市估算');
  assert.equal(d.change,'.025'); assert.equal(d.asOf,'2026-10-20T11:29:00+08:00');
});

test('unchanged JSON preserves identities, changed quotes preserve NAV history and other funds',()=>{
  const old={funds:[{id:'a',nav:{value:'1'},history:[{id:'n',value:'1'}],quote:{value:'1.1'}},{id:'b'}],orders:[]};
  assert.equal(shareData(old,structuredClone(old)),old);
  const next=structuredClone(old);next.funds[0].quote.value='1.2';const shared=shareData(old,next);
  assert.notEqual(shared,old);assert.equal(shared.orders,old.orders);assert.equal(shared.funds[1],old.funds[1]);
  assert.equal(shared.funds[0].history,old.funds[0].history);assert.notEqual(shared.funds[0].quote,old.funds[0].quote);
});
test('deletions, empty arrays, null and array length changes are not hidden',()=>{
  for(const [a,b] of [[{x:1},{}],[[1,2],[1]],[{x:1},{x:null}],[null,[]],[[],{}]])assert.deepEqual(shareData(a,b),b);
});
test('only server clock changes do not repaint; meaningful updates retain the new clock',()=>{
  const old={now:'a',funds:[{id:'a'}]};assert.equal(shareSnapshot(old,{...structuredClone(old),now:'b'}),old);
  const next=shareSnapshot(old,{now:'c',funds:[{id:'b'}]});assert.equal(next.now,'c');assert.equal(next.funds[0].id,'b');
});
test('plan identity and expiry metadata do not repaint, reference content still updates',()=>{
  const old=displayPlan({id:'1',generated_at:'a',expires_at:'b',items:[{amount:'10'}],daily_t_items:[]});
  assert.equal(shareData(old,displayPlan({id:'2',generated_at:'c',expires_at:'d',items:[{amount:'10'}],daily_t_items:[]})),old);
  assert.notEqual(shareData(old,displayPlan({items:[{amount:'11'}],daily_t_items:[]})),old);
  assert.equal(displayPlan(null),null);
});
test('references must match account, ledger, profile, quote/baseline and product versions',()=>{
  const state={mode:'real',revision:1,funds:[{id:'a',daily_t:{profile:{_version:2},today:{quote:{id:'q'},baseline:{id:'b'}}},strategy:{_version:3},rules:{_version:4},estimate:{id:'q'}}]};
  const plan={mode:'real',ledger_version:1,daily_t_items:[{fund_id:'a',profile_version:2,observation_id:'q',baseline_id:'b'}],items:[{fund_id:'a',config_version:3,rule_version:4,observation_id:'q'}]};
  assert.equal(planMatchesSnapshot(state,plan),true);
  for(const field of ['mode','ledger_version'])assert.equal(planMatchesSnapshot(state,{...plan,[field]:'wrong'}),false);
  for(const field of ['fund_id','profile_version','observation_id','baseline_id']){
    const changed=structuredClone(plan);changed.daily_t_items[0][field]='wrong';assert.equal(planMatchesSnapshot(state,changed),false);
  }
  for(const field of ['config_version','rule_version','observation_id']){
    const changed=structuredClone(plan);changed.items[0][field]='wrong';assert.equal(planMatchesSnapshot(state,changed),false);
  }
});

test('investment plans use their own profile version and reject disabled or obsolete references',()=>{
  const fund={id:'a',investment_plan_profile:{enabled:true,_version:7},daily_t:{profile:{_version:2},today:{quote:{id:'q'},baseline:{id:'b'}}}};
  const state={mode:'real',revision:1,funds:[fund]};
  const plan={mode:'real',ledger_version:1,daily_t_items:[{fund_id:'a',level:'PLAN',profile_version:7,observation_id:'q',baseline_id:'b'}],items:[]};
  assert.equal(planMatchesSnapshot(state,plan),true);
  const previousIntensity=structuredClone(plan);previousIntensity.daily_t_items[0].level='CAUTIOUS';previousIntensity.daily_t_items[0].profile_version=2;
  assert.equal(planMatchesSnapshot(state,previousIntensity),false);
  for(const profile of [{enabled:true,_version:8},{enabled:false,_version:7},undefined]){
    assert.equal(planMatchesSnapshot({...state,funds:[{...fund,investment_plan_profile:profile}]},plan),false);
  }
  fund.investment_plan_profile.enabled=false;
  assert.equal(planMatchesSnapshot(state,previousIntensity),true);
});

test('investment plan recording preserves backend buy money or sell units and always uses T bucket',()=>{
  const fund={id:'a',investment_plan_profile:{enabled:true,_version:7}};
  const reference={fund_id:'a',level:'PLAN',profile_version:7,can_apply:true,action:'BUY',amount:'200.00',units:null};
  assert.deepEqual(investmentPlanRecord(fund,reference),{fund_id:'a',side:'BUY',bucket:'TACTICAL',requested:'200.00'});
  assert.deepEqual(investmentPlanRecord(fund,{...reference,action:'SELL',amount:'199.98',units:'234.56'}),
    {fund_id:'a',side:'SELL',bucket:'TACTICAL',requested:'234.56'});
});

test('lunch and close scenarios, missing data and candidate sells cannot prefill an operation',()=>{
  const fund={id:'a',investment_plan_profile:{enabled:true,_version:7}};
  const reference={fund_id:'a',level:'PLAN',profile_version:7,can_apply:true,action:'BUY',amount:'200.00'};
  for(const patch of [{can_apply:false},{preview_only:true},{action:'HOLD'},{action:'WAIT',candidate_sell_impact:{expected_realized_pnl:'4'}},
    {action:'SELL',units:null},{action:'SELL',units:'0'},{amount:null},{amount:'NaN'},{amount:'-1'},
    {profile_version:6},{level:'CAUTIOUS'},{fund_id:'b'}])assert.equal(investmentPlanRecord(fund,{...reference,...patch}),null);
  assert.equal(investmentPlanRecord({...fund,investment_plan_profile:{enabled:false,_version:7}},reference),null);
  assert.equal(investmentPlanRecord(fund),null);
});

function guardedPlan() {
  const history = [{id:'n1',value_kind:'OFFICIAL_NAV',effective_date:'2026-10-21',observed_at:'2026-10-21T12:00:00Z'},
    {id:'n2',value_kind:'OFFICIAL_NAV',effective_date:'2026-10-22',observed_at:'2026-10-22T12:00:00Z'}];
  const fund={id:'ct',investment_plan_profile:{enabled:true,_version:3,template:'DRAWDOWN_GUARD_V2'},history,
    daily_t:{market:{date:'2026-10-23'},today:{quote:{id:'q'},baseline:{id:'n2'}}}};
  const item={fund_id:'ct',level:'PLAN',profile_version:3,observation_id:'q',baseline_id:'n2',can_apply:true,
    action:'BUY',amount:'150.00',plan:{template:'DRAWDOWN_GUARD_V2',risk:{input_ids:['n1','n2'],
      input_basis:{history_count:2,history_end_date:'2026-10-22',quote_id:'q'}}}};
  return {fund,item,state:{mode:'real',revision:8,now:'2026-10-23T06:00:00Z',funds:[fund]},
    plan:{mode:'real',ledger_version:8,daily_t_items:[item],items:[]}};
}

test('changing template rejects a prior daily plan even if its profile version and quote are equal',()=>{
  const {fund,item}=guardedPlan();
  assert.equal(investmentPlanMatchesFund(fund,item),true);
  assert.equal(investmentPlanMatchesFund(fund,{...item,plan:{template:'DAILY_LADDER_V1'}}),false);
  assert.equal(investmentPlanMatchesFund({...fund,investment_plan_profile:{...fund.investment_plan_profile,template:'DAILY_LADDER_V1'}},item),false);
  assert.equal(investmentPlanRecord(fund,{...item,plan:{template:'DAILY_LADDER_V1'}}),null);
});

test('multiday references require the same formally disclosed risk history and quote basis',()=>{
  const {fund,item,state,plan}=guardedPlan();
  assert.equal(planMatchesSnapshot(state,plan),true);
  assert.deepEqual(investmentPlanRecord(fund,item),{fund_id:'ct',side:'BUY',bucket:'TACTICAL',requested:'150.00'});
  for(const changed of [fund.history.slice(1),[...fund.history,{id:'n3',value_kind:'OFFICIAL_NAV',effective_date:'2026-10-23',observed_at:'2026-10-23T05:00:00Z'}],
    [fund.history[0],{...fund.history[1],id:'revised'}]]) {
    assert.equal(planMatchesSnapshot({...state,funds:[{...fund,history:changed}]},plan),false);
  }
  for(const patch of [{history_count:1},{history_end_date:'2026-10-21'},{quote_id:'other'}]) {
    const changed=structuredClone(plan);Object.assign(changed.daily_t_items[0].plan.risk.input_basis,patch);
    assert.equal(planMatchesSnapshot(state,changed),false);
  }
});

test('future observations do not contaminate current multiday risk identity',()=>{
  const {fund,state,plan}=guardedPlan();
  const future={id:'future',value_kind:'OFFICIAL_NAV',effective_date:'2026-10-23',observed_at:'2026-10-23T08:00:00Z'};
  assert.equal(planMatchesSnapshot({...state,funds:[{...fund,history:[...fund.history,future]}]},plan),true);
  assert.equal(planMatchesSnapshot({...state,now:'2026-10-23T09:00:00Z',funds:[{...fund,history:[...fund.history,future]}]},plan),false);
  const notDisclosed={...future,observed_at:'2026-10-23T05:00:00Z',published_at:'2026-10-23T08:00:00Z'};
  assert.equal(planMatchesSnapshot({...state,funds:[{...fund,history:[...fund.history,notDisclosed]}]},plan),true);
});

test('known risk remains displayable without a quote while paused amounts cannot prepare a trade',()=>{
  const {fund,item}=guardedPlan();
  fund.daily_t.today={quote:null,baseline:{id:'n2'}};
  Object.assign(item,{observation_id:null,can_apply:false,action:'WAIT',amount:null});
  Object.assign(item.plan.risk,{state:'PAUSED',label:'暂停加仓',input_basis:{history_count:2,history_end_date:'2026-10-22',quote_id:null}});
  assert.equal(investmentPlanMatchesFund(fund,item),true);
  assert.equal(investmentPlanRecord(fund,item),null);
  assert.equal(item.plan.risk.label,'暂停加仓');
});

test('blocked risk-data explanations are visible but never become an applicable amount',()=>{
  const {fund,item}=guardedPlan();
  Object.assign(item,{can_apply:false,action:'WAIT',amount:null});
  item.plan.risk={ready:false,status:'RISK_CORPORATE_REVIEW',input_ids:[],
    input_basis:{history_count:0,history_end_date:null,history_digest:null}};
  assert.equal(investmentPlanMatchesFund(fund,item),true);
  assert.equal(investmentPlanRecord(fund,item),null);
  item.can_apply=true;
  assert.equal(investmentPlanMatchesFund(fund,item),false);
});
