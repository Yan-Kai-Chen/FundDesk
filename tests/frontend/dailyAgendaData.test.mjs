import test from 'node:test';
import assert from 'node:assert/strict';
import {dailyAgendaMatchesSnapshot} from '../../src/frontend/src/dailyAgendaData.ts';

function fixture() {
  const state = {business_date:'2026-10-23',revision:17,funds:[{id:'a',units:'1234.56',rules:{_version:4},
    investment_plan_profile:{_version:7},daily_t:{profile:{_version:3},market:{date:'2026-10-23'},today:{quote:{id:'q'},baseline:{id:'b'}}}}]};
  const agenda = {date:'2026-10-23',revision:17,snapshot_basis:[{fund_id:'a',units:'1234.56',rule_version:4,
    t_profile_version:3,investment_plan_version:7,quote_id:'q',baseline_id:'b',market_date:'2026-10-23'}]};
  return {state,agenda};
}

test('matching independent GET results pass without altering either input',()=>{
  const {state,agenda}=fixture(),before=structuredClone({state,agenda});
  assert.equal(dailyAgendaMatchesSnapshot(state,agenda),true);
  assert.deepEqual({state,agenda},before);
});

test('a quote or baseline update at the same ledger revision invalidates the agenda',()=>{
  for(const key of ['quote','baseline']){
    const {state,agenda}=fixture();state.funds[0].daily_t.today[key].id='fresh';
    assert.equal(state.revision,agenda.revision);
    assert.equal(dailyAgendaMatchesSnapshot(state,agenda),false);
  }
});

test('same-revision intensity, investment plan, fee rule or units changes invalidate the agenda',()=>{
  const changes=[fund=>fund.daily_t.profile._version++,fund=>fund.investment_plan_profile._version++,
    fund=>fund.rules._version++,fund=>fund.units='1234.57'];
  for(const change of changes){
    const {state,agenda}=fixture();change(state.funds[0]);
    assert.equal(state.revision,agenda.revision);
    assert.equal(dailyAgendaMatchesSnapshot(state,agenda),false);
  }
});

test('ledger/business day/market day and added or removed funds invalidate the agenda',()=>{
  for(const mutate of [state=>state.revision++,state=>state.business_date='2026-10-24',
    state=>state.funds[0].daily_t.market.date='2026-10-24',state=>state.funds.push({id:'new'}),state=>state.funds=[]]){
    const {state,agenda}=fixture();mutate(state);assert.equal(dailyAgendaMatchesSnapshot(state,agenda),false);
  }
});

test('missing/duplicate/malformed basis fails closed instead of accepting an old response',()=>{
  const {state,agenda}=fixture();
  for(const invalid of [undefined,null,{}, {...agenda,snapshot_basis:undefined},{...agenda,snapshot_basis:[null]},
    {...agenda,snapshot_basis:[{fund_id:'a'}]},{...agenda,snapshot_basis:[{...agenda.snapshot_basis[0],fund_id:'other'}]}]){
    assert.equal(dailyAgendaMatchesSnapshot(state,invalid),false);
  }
  const duplicate=structuredClone(state);duplicate.funds.push(duplicate.funds[0]);
  assert.equal(dailyAgendaMatchesSnapshot(duplicate,{...agenda,snapshot_basis:[...agenda.snapshot_basis,...agenda.snapshot_basis]}),false);
});

test('matching is independent of fund order and supports genuinely missing quote/profile inputs',()=>{
  const {state,agenda}=fixture();
  state.funds.push({id:'no-quote',units:'0',daily_t:{market:{date:'2026-10-23'}}});
  agenda.snapshot_basis.unshift({fund_id:'no-quote',units:'0',rule_version:null,t_profile_version:null,
    investment_plan_version:null,quote_id:null,baseline_id:null,market_date:'2026-10-23'});
  assert.equal(dailyAgendaMatchesSnapshot(state,agenda),true);
});
