import test from 'node:test';
import assert from 'node:assert/strict';
import { researchReason, selectionMatches, researchItem, directionLabel, rate } from '../../src/frontend/src/researchView.ts';

test('saved methods compare as a set; changing selection is not a new analysis', () => {
  const run={selected_methods:['TREND','VALUE'],observations:{methods:{TREND:{items:[{fund_id:'a',status:'WAIT'}]}}}};
  assert.equal(selectionMatches(['VALUE','TREND'],run),true);
  assert.equal(selectionMatches(['TREND'],run),false);
  assert.equal(selectionMatches(['TREND'],null),false);
  assert.equal(researchItem(run,'TREND','a').status,'WAIT');
  assert.equal(researchItem(run,'VALUE','a'),undefined);
});
test('missing evidence and inapplicability are explained without a hold or probability claim', () => {
  assert.equal(researchReason('MARKET_MAPPING_UNSUPPORTED'),'跨市场产品暂不套用此版本');
  assert.match(researchReason('fund-a:MISSING_TOTAL_RETURN_SESSION:2026-10-20'),/对齐/);
  assert.match(researchReason('ASSET_CAP:fund-a_PROTECTED_OR_LOCKED_FLOOR'),/底仓/);
  assert.equal(directionLabel.INCREASE,'增加');
  assert.equal(rate('0.16'),'16.00%');
  assert.equal(rate(null),'—');
});
