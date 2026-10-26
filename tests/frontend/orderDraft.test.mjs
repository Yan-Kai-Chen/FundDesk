import test from 'node:test';
import assert from 'node:assert/strict';
import { readOrderDraft, saveOrderDraft, clearOrderDraft } from '../../src/frontend/src/orderDraft.ts';
const storage = () => { const m = new Map(); return {getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)}; };
const fields = {fund_id:'fund-1',side:'SELL',bucket:'TACTICAL',requested:'30',time_precision:'BEFORE_CUTOFF',submitted_date:'2026-10-22',paid_immediately:false,auto_confirm:true,lot_batch1:'10'};
test('draft isolates accounts and retains actual time, explicit payment and retry key',()=>{
  const s=storage();saveOrderDraft(s,'real',fields,'retry-1',1000);
  assert.equal(readOrderDraft(s,'demo',1001),null);
  const d=readOrderDraft(s,'real',1001);assert.deepEqual(d.fields,fields);assert.equal(d.key,'retry-1');
  clearOrderDraft(s,'real');assert.equal(readOrderDraft(s,'real',1001),null);
});
test('draft is bounded, expires after 30 days, and excludes snapshots and prices',()=>{
  const s=storage();saveOrderDraft(s,'real',{...fields,preview:'READY',nav:'1.2',csrf_token:'secret',ledger:{units:'30'}},'retry-1',1000);
  assert.deepEqual(readOrderDraft(s,'real',1001).fields,fields);
  assert.equal(readOrderDraft(s,'real',1000+31*86400000),null);assert.equal(s.getItem('funding-order-draft-real'),null);
  s.setItem('funding-order-draft-real','broken');assert.equal(readOrderDraft(s,'real'),null);
  s.setItem('funding-order-draft-real','x'.repeat(24001));assert.equal(readOrderDraft(s,'real'),null);
});
test('unavailable browser storage does not block entering a transaction',()=>{
  const s={getItem(){throw Error()},setItem(){throw Error()},removeItem(){throw Error()}};
  assert.equal(readOrderDraft(s,'real'),null);assert.equal(saveOrderDraft(s,'real',fields,'retry-1'),false);
});
