import test from 'node:test';
import assert from 'node:assert/strict';
import { pendingOrders, needsOrderInput, dayCoverageLabel } from '../../src/frontend/src/overviewData.ts';
test('normal automated waits are visible without being flagged as missing information',()=>{
  const wait={id:'wait',status:'SUBMITTED',automation:{status:'WAIT_NAV'}};
  const paid={id:'pay',status:'SUBMITTED',automation:{status:'WAIT_PAYMENT'}};
  const done={id:'done',automation:{status:'AUTO_BOOKED'}};
  const cancel={...wait,cancel_requested:true,status:'CANCEL_REQUESTED'};
  const paused={status:'ACCEPTED',automation:{status:'PAUSED'}};
  assert.deepEqual(pendingOrders([wait,paid,done,{...wait,closed:true},cancel,paused]),[wait,paid,cancel,paused]);
  assert.equal(needsOrderInput(wait),false);assert.equal(needsOrderInput(paid),true);
});
test('partial, no-open and no-holdings totals are not described as complete or zero',()=>{
  assert.equal(dayCoverageLabel({status:'NOT_OPEN'}),'没开盘');
  assert.match(dayCoverageLabel({covered_count:4,holding_count:6,complete:false}),/合计待完整/);
  assert.equal(dayCoverageLabel({covered_count:6,holding_count:6,complete:true}),'6 / 6 只已更新');
  assert.equal(dayCoverageLabel({status:'EMPTY'}),'暂无持仓');
});
