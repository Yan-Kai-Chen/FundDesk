import type { Row } from './api';
import type { SidebarCatalog } from './sidebarCatalog';

export type RecordFilters = {
  query: string;
  fundId: string;
  side: 'ALL' | 'BUY' | 'SELL';
  bucket: 'ALL' | 'BASE' | 'TACTICAL';
  status: 'ALL' | 'PENDING' | 'BOOKED' | 'CANCELED';
  period: 'ALL' | 'TODAY' | '7D' | '30D';
};

export const DEFAULT_RECORD_FILTERS: RecordFilters = {
  query: '', fundId: '', side: 'ALL', bucket: 'ALL', status: 'ALL', period: 'ALL',
};

export function normalizeRecordFilters(value: unknown): RecordFilters {
  const input = value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
  return {
    query: typeof input.query === 'string' ? input.query.slice(0, 200) : '',
    fundId: typeof input.fundId === 'string' ? input.fundId.slice(0, 120) : '',
    side: ['BUY', 'SELL'].includes(input.side) ? input.side : 'ALL',
    bucket: ['BASE', 'TACTICAL'].includes(input.bucket) ? input.bucket : 'ALL',
    status: ['PENDING', 'BOOKED', 'CANCELED'].includes(input.status) ? input.status : 'ALL',
    period: ['TODAY', '7D', '30D'].includes(input.period) ? input.period : 'ALL',
  };
}

export function hasRecordFilters(filters: RecordFilters): boolean {
  return !!filters.query.trim() || !!filters.fundId || filters.side !== 'ALL' ||
    filters.bucket !== 'ALL' || filters.status !== 'ALL' || filters.period !== 'ALL';
}

type Preferences = Pick<Storage, 'getItem' | 'setItem'>;
const storageKey = (mode: string) => `funding-record-filters-${mode}`;

export function readRecordFilters(storage: Preferences | undefined, mode: string): RecordFilters {
  try { return normalizeRecordFilters(JSON.parse(storage?.getItem(storageKey(mode)) || 'null')); }
  catch { return { ...DEFAULT_RECORD_FILTERS }; }
}

export function writeRecordFilters(storage: Preferences | undefined, mode: string, value: RecordFilters) {
  try { storage?.setItem(storageKey(mode), JSON.stringify(normalizeRecordFilters(value))); }
  catch { /* Filters remain usable when browser storage is unavailable. */ }
}

export function recordStatus(order: Row): 'PENDING' | 'BOOKED' | 'CANCELED' {
  // A partially filled order can then be canceled; retain its final disposition.
  if (['ORDER_CANCELED', 'ORDER_REJECTED'].includes(order.close_reason) ||
      ['CANCELED', 'CANCELLED', 'REJECTED'].includes(order.status)) return 'CANCELED';
  if (order.cancel_requested || order.status === 'PARTIALLY_CONFIRMED') return 'PENDING';
  if (order.closed && (Number(order.confirmed_units) > 0 || order.status === 'CONFIRMED')) return 'BOOKED';
  return 'PENDING';
}

export function recordBeijingDate(value: unknown): string {
  if (typeof value !== 'string' || !value) return '';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return '';
  return new Date(timestamp + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

function dateFloor(businessDate: string, period: RecordFilters['period']): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(businessDate)) return '';
  const timestamp = Date.parse(`${businessDate}T00:00:00Z`);
  if (!Number.isFinite(timestamp)) return '';
  const days = period === '30D' ? 29 : period === '7D' ? 6 : 0;
  return new Date(timestamp - days * 86400000).toISOString().slice(0, 10);
}

export function filterRecordOrders(orders: Row[], funds: Row[], catalog: SidebarCatalog | undefined,
  filters: RecordFilters, businessDate: string): Row[] {
  const term = filters.query.trim().toLocaleLowerCase();
  const names = new Map(funds.map(fund => {
    const presentation = catalog?.funds.find(row => row.fund_id === fund.id);
    return [fund.id, `${fund.name || ''} ${fund.fund_code || ''} ${presentation?.display_name || ''} ${presentation?.theme || ''}`];
  }));
  const floor = filters.period === 'ALL' ? '' : dateFloor(businessDate, filters.period);
  return orders.map((order, index) => ({ order, index, date: recordBeijingDate(order.submitted_at) }))
    .filter(({ order, date }) => (!filters.fundId || order.fund_id === filters.fundId) &&
      (filters.side === 'ALL' || order.side === filters.side) &&
      (filters.bucket === 'ALL' || order.bucket === filters.bucket) &&
      (filters.status === 'ALL' || recordStatus(order) === filters.status) &&
      (filters.period === 'ALL' || (!!floor && !!date && date >= floor && date <= businessDate)) &&
      (!term || `${names.get(order.fund_id) || ''} ${order.external_order_id || ''} ${order.id || ''}`.toLocaleLowerCase().includes(term)))
    .sort((a, b) => {
      const dateOrder = b.date.localeCompare(a.date);
      if (dateOrder) return dateOrder;
      const aUnknown = a.order.time_precision === 'BEFORE_CUTOFF';
      const bUnknown = b.order.time_precision === 'BEFORE_CUTOFF';
      // Unknown intraday windows follow timed rows on that date, in original
      // order. Midnight is a backend sort representation, never an actual time.
      if (aUnknown !== bUnknown) return aUnknown ? 1 : -1;
      if (!aUnknown) {
        const aTime = Date.parse(a.order.submitted_at), bTime = Date.parse(b.order.submitted_at);
        if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) return bTime - aTime;
      }
      return a.index - b.index;
    }).map(({ order }) => order);
}
