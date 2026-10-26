import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_RECORD_FILTERS, filterRecordOrders, hasRecordFilters, normalizeRecordFilters, readRecordFilters,
  writeRecordFilters, recordStatus, recordBeijingDate } from '../../src/frontend/src/recordFilters.ts';

const funds = [{ id: 'ai', name: '广发沪港深精选混合C', fund_code: '012183' }, { id: 'gold', name: '广发上海金ETF联接C', fund_code: '008987' }];
const catalog = { groups: [], _version: 1, funds: [{ fund_id: 'ai', display_name: '我的AI', theme: 'AI应用', group_id: null }] };
const base = { fund_id: 'ai', side: 'BUY', bucket: 'BASE', status: 'SUBMITTED', confirmed_units: '0', closed: false };
const orders = [
  { ...base, id: 'old', submitted_at: '2026-09-23T06:00:00Z' },
  { ...base, id: 'pending', external_order_id: 'ALIPAY-XYZ', submitted_at: '2026-10-22T16:00:00Z' },
  { ...base, id: 'booked', fund_id: 'gold', side: 'SELL', bucket: 'TACTICAL', submitted_at: '2026-10-23T06:00:00Z', closed: true, status: 'CONFIRMED', confirmed_units: '10', unsettled: '100', automation: { status: 'AUTO_BOOKED' } },
  { ...base, id: 'canceled', submitted_at: '2026-10-17T07:00:00Z', closed: true, close_reason: 'ORDER_CANCELED', status: 'CANCELED' },
  { ...base, id: 'partial', submitted_at: '2026-10-16T07:00:00Z', status: 'PARTIALLY_CONFIRMED', confirmed_units: '1' },
];
const select = (filters = {}, rows = orders, date = '2026-10-23') => filterRecordOrders(rows, funds, catalog, { ...DEFAULT_RECORD_FILTERS, ...filters }, date).map(row => row.id);

test('search matches names, aliases, codes and full external or local order identifiers', () => {
  for (const query of ['沪港深', '我的ai', ' 012183 ', 'AI应用', 'alipay-xyz', 'pending']) assert.ok(select({ query }).includes('pending'));
  assert.deepEqual(select({ query: '008987' }), ['booked']);
  assert.deepEqual(select({ query: '不存在' }), []);
});

test('combined selectors use order state; optional bank settlement does not make a booked sale pending', () => {
  assert.deepEqual(select({ fundId: 'gold', side: 'SELL', bucket: 'TACTICAL', status: 'BOOKED' }), ['booked']);
  assert.deepEqual(select({ status: 'PENDING' }), ['pending', 'partial', 'old']);
  assert.deepEqual(select({ status: 'CANCELED' }), ['canceled']);
  assert.equal(recordStatus({ ...base, status: 'CONFIRMED', closed: true, confirmed_units: '1', close_reason: 'ORDER_REJECTED' }), 'CANCELED');
  assert.equal(recordStatus({ ...base, cancel_requested: true, automation: { status: 'PAUSED' } }), 'PENDING');
});

test('date ranges are inclusive Beijing calendar days, including midnight across UTC dates', () => {
  assert.equal(recordBeijingDate('2026-10-22T16:00:00Z'), '2026-10-23');
  assert.deepEqual(select({ period: 'TODAY' }), ['booked', 'pending']);
  assert.deepEqual(select({ period: '7D' }), ['booked', 'pending', 'canceled']);
  assert.deepEqual(select({ period: '30D' }), ['booked', 'pending', 'canceled', 'partial']);
  assert.deepEqual(select({ period: 'TODAY' }, [{ ...base, id: 'future', submitted_at: '2026-10-23T16:00:00Z' }]), []);
  assert.deepEqual(select({ period: 'TODAY' }, orders, ''), []);
});

test('sort uses actual submission, keeps ties stable and never mutates uncertain time windows or input arrays', () => {
  const rows = [
    { ...base, id: 'unknown-a', submitted_at: '2026-10-22T16:00:00Z', time_precision: 'BEFORE_CUTOFF' },
    { ...base, id: 'equal-a', submitted_at: '2026-10-23T05:00:00Z', recorded_at: '2026-10-24T08:00:00Z' },
    { ...base, id: 'unknown-b', submitted_at: '2026-10-22T16:00:00Z', time_precision: 'BEFORE_CUTOFF' },
    { ...base, id: 'equal-b', submitted_at: '2026-10-23T05:00:00Z', recorded_at: '2026-10-23T08:00:00Z' },
    { ...base, id: 'later', submitted_at: '2026-10-23T06:00:00Z' },
  ];
  const before = structuredClone(rows);
  assert.deepEqual(select({}, rows), ['later', 'equal-a', 'equal-b', 'unknown-a', 'unknown-b']);
  assert.deepEqual(rows, before);
  assert.equal(filterRecordOrders(rows, funds, catalog, DEFAULT_RECORD_FILTERS, '2026-10-23').at(-1), rows[2]);
});

test('invalid timestamps sort last and never match a finite date range', () => {
  const rows = [{ ...base, id: 'bad', submitted_at: 'invalid' }, { ...base, id: 'good', submitted_at: '2026-10-23T06:00:00Z' }];
  assert.deepEqual(select({}, rows), ['good', 'bad']);
  assert.deepEqual(select({ period: 'TODAY' }, rows), ['good']);
  assert.equal(recordBeijingDate(undefined), '');
});

test('filter preferences are bounded, validated and separated between real and demo', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value) };
  writeRecordFilters(storage, 'real', { ...DEFAULT_RECORD_FILTERS, side: 'BUY', query: '012183' });
  assert.equal(readRecordFilters(storage, 'real').side, 'BUY');
  assert.deepEqual(readRecordFilters(storage, 'demo'), DEFAULT_RECORD_FILTERS);
  writeRecordFilters(storage, 'demo', { ...DEFAULT_RECORD_FILTERS, bucket: 'TACTICAL' });
  writeRecordFilters(storage, 'real', DEFAULT_RECORD_FILTERS);
  assert.deepEqual(readRecordFilters(storage, 'real'), DEFAULT_RECORD_FILTERS);
  assert.equal(readRecordFilters(storage, 'demo').bucket, 'TACTICAL');
  assert.deepEqual(normalizeRecordFilters({ query: 1, fundId: [], side: 'INVALID', status: 'AUTO_BOOKED', period: '100D' }), DEFAULT_RECORD_FILTERS);
  assert.equal(normalizeRecordFilters({ query: 'x'.repeat(400) }).query.length, 200);
  assert.equal(hasRecordFilters({ ...DEFAULT_RECORD_FILTERS, query: '   ' }), false);
  assert.equal(hasRecordFilters({ ...DEFAULT_RECORD_FILTERS, status: 'PENDING' }), true);
});

test('malformed or unavailable local storage falls back without blocking records', () => {
  assert.deepEqual(readRecordFilters({ getItem: () => '{bad', setItem: () => {} }, 'real'), DEFAULT_RECORD_FILTERS);
  const unavailable = { getItem: () => { throw Error('blocked'); }, setItem: () => { throw Error('full'); } };
  assert.deepEqual(readRecordFilters(unavailable, 'real'), DEFAULT_RECORD_FILTERS);
  assert.doesNotThrow(() => writeRecordFilters(unavailable, 'real', DEFAULT_RECORD_FILTERS));
});
