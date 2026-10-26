import test from 'node:test';
import assert from 'node:assert/strict';
import { fundSections, sidebarFund, removeSidebarGroup, moveSidebarGroup } from '../../src/frontend/src/sidebarCatalog.ts';

const funds = [{ id: 'a', name: '基金正式全名', fund_code: '012183', watching: true },
  { id: 'b', name: '尚未分类', fund_code: '000002', watching: true },
  { id: 'c', name: '未自选的基金', watching: false }];
const catalog = { _version: 1, groups: [{ id: 'tech', name: '科技成长' }, { id: 'mine', name: '我的分类' }],
  funds: [{ fund_id: 'a', display_name: '我的AI', theme: 'AI应用', group_id: 'tech' }] };

test('grouping preserves custom order and empty categories; new funds stay unclassified', () => {
  const grouped = fundSections(funds, catalog);
  assert.deepEqual(grouped.map(g => g.id), ['tech', 'mine', 'unclassified']);
  assert.deepEqual(grouped.flatMap(g => g.funds.map(f => f.id)), ['a', 'b']);
  assert.equal(grouped[0].funds[0].sidebar.display_name, '我的AI');
  assert.equal(sidebarFund(catalog, funds[1]).display_name, '尚未分类');
});
test('search reaches collapsed groups through canonical name, alias, code, theme or category', () => {
  for (const query of ['正式全名', '我的ai', ' 012183 ', 'AI应用', '科技成长']) {
    assert.deepEqual(fundSections(funds, catalog, query).flatMap(g => g.funds.map(f => f.id)), ['a']);
  }
  assert.deepEqual(fundSections(funds, catalog, '无匹配'), []);
});
test('category removal retains the fund, alias and theme; ordering changes only categories', () => {
  const removed = removeSidebarGroup(catalog, 'tech');
  assert.equal(removed.funds[0].group_id, null);
  assert.equal(removed.funds[0].theme, 'AI应用');
  assert.equal(catalog.funds[0].group_id, 'tech');
  assert.equal(fundSections(funds, removed).at(-1).funds[0].id, 'a');
  const moved = moveSidebarGroup(catalog, 1, -1);
  assert.deepEqual(moved.groups.map(g => g.id), ['mine', 'tech']);
  assert.equal(moved.funds, catalog.funds);
  assert.equal(moveSidebarGroup(catalog, 0, -1), catalog);
});
