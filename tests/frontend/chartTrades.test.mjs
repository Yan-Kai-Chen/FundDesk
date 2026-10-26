import test from 'node:test';
import assert from 'node:assert/strict';
import { collectChartTrades, groupChartTrades } from '../../src/frontend/src/chartTrades.ts';

const nav = date => ({effective_date:date,value_kind:'OFFICIAL_NAV',value:'1.00'});
const trade = (id,date,side='BUY') => ({id,date,side,basis:'test'});

test('same-date orders group without duplicating original screenshot rows',()=>{
  const history=[nav('2026-09-01'),nav('2026-09-02')];
  const groups=groupChartTrades(history,[trade('a','2026-09-01'),trade('a','2026-09-01'),trade('b','2026-09-01'),trade('c','2026-09-01','SELL')]);
  assert.equal(groups.length,2);
  assert.deepEqual(groups.map(g=>[g.side,g.index,g.trades.length]),[['BUY',0,2],['SELL',0,1]]);
});
test('missing NAV dates and trades outside the visible range never snap to a nearby date',()=>{
  const groups=groupChartTrades([nav('2026-09-01'),nav('2026-09-03')],[trade('a','2026-08-31'),trade('b','2026-09-02'),trade('c','2026-09-03')]);
  assert.deepEqual(groups.map(g=>g.date),['2026-09-03']);
});
test('intraday estimates do not acquire official NAV trade markers',()=>{
  assert.deepEqual(groupChartTrades([{...nav('2026-09-01'),value_kind:'THIRD_PARTY_ESTIMATE'}],[trade('a','2026-09-01')]),[]);
});
test('accepted historical reconstruction takes precedence over its original statement copy',()=>{
  const trades=collectChartTrades({id:'f',tracking:{historical_rows:[{id:'a',at:'2026-09-01 14:00:00',side:'BUY',cost:'100',units:'90'}]},statement:{rows:[{id:'a',displayed_at:'2026-09-01 14:00:00',side:'BUY',unit:'CNY',value:'100'}]}},[]);
  assert.equal(trades.length,1); assert.equal(trades[0].date,'2026-09-01');
  assert.match(trades[0].basis,/历史还原/);
});
test('new confirmed portions use confirmed NAV date; submissions and other funds do not appear',()=>{
  const orders=[{fund_id:'f',side:'SELL',submitted_at:'2026-09-01T08:00:00Z',confirmations:[{event_id:'c',nav_date:'2026-09-02',confirm_date:'2026-09-03',units:'10',net:'12'}]},
    {fund_id:'f',side:'BUY',confirmations:[]},{fund_id:'other',side:'BUY',confirmations:[{event_id:'x',nav_date:'2026-09-02'}]}];
  const trades=collectChartTrades({id:'f'},orders);
  assert.deepEqual(trades.map(t=>[t.id,t.date,t.side,t.amount]),[['c','2026-09-02','SELL','12']]);
});
test('automatic NAV bookkeeping is not labelled as a channel receipt',()=>{
  const trades=collectChartTrades({id:'f'},[{fund_id:'f',side:'BUY',confirmations:[{
    event_id:'auto',nav_date:'2026-10-22',gross:'200',units:'108.56',confirmation_basis:'AUTO_OFFICIAL_NAV'
  },{event_id:'actual',nav_date:'2026-10-22',gross:'100',units:'54.28',confirmation_basis:'CHANNEL_RECORD'}]}]);
  assert.match(trades[0].basis,/自动计算/);assert.doesNotMatch(trades[0].basis,/渠道确认/);
  assert.match(trades[1].basis,/渠道确认/);
});
test('closed erroneous screenshot rows are excluded; share units remain shares',()=>{
  const trades=collectChartTrades({id:'f',statement:{rows:[
    {id:'bad',status:'CLOSED',side:'BUY',displayed_at:'2026-09-01 14:00:00',value:'500',unit:'CNY'},
    {id:'good',status:'PENDING_SETTLEMENT',side:'SELL',displayed_at:'2026-09-02 14:00:00',value:'10',unit:'FUND_UNIT'}]}},[]);
  assert.equal(trades.length,1);assert.equal(trades[0].units,'10');assert.equal(trades[0].amount,undefined);
});
