import test from 'node:test';
import assert from 'node:assert/strict';
import { shanghaiInput, submittedTime, orderTimeLabel } from '../../src/frontend/src/api.ts';

test('trade entry always displays and submits Beijing time regardless of Mac timezone',()=>{
  const old=process.env.TZ;
  try {
    for(const zone of ['Asia/Tokyo','UTC','America/Los_Angeles']) {
      process.env.TZ=zone;
      assert.equal(shanghaiInput('2026-10-22T06:30:00Z'),'2026-10-22T14:30');
      assert.equal(submittedTime('2026-10-22T14:30'),'2026-10-22T06:30:00.000Z');
    }
  } finally { if(old===undefined)delete process.env.TZ;else process.env.TZ=old; }
});
test('seconds and midnight survive late-entry editing without a timezone shift',()=>{
  assert.equal(submittedTime('2026-10-22T14:59:59'),'2026-10-22T06:59:59.000Z');
  assert.equal(shanghaiInput('2026-10-21T16:00:00Z'),'2026-10-22T00:00');
  assert.equal(submittedTime('2026-10-22T00:00'),'2026-10-21T16:00:00.000Z');
});
test('an uncertain before-15:00 order displays its known window, never a made-up midnight',()=>{
  const label=orderTimeLabel({submitted_at:'2026-10-21T16:00:00Z',time_precision:'BEFORE_CUTOFF'});
  assert.match(label,/10\/22/);assert.match(label,/15:00 前/);assert.doesNotMatch(label,/00:00/);
});
