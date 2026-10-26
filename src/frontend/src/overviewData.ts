import type { Row } from './api';

export function pendingOrders(orders: Row[]) {
  return orders.filter(o => !o.closed && !o.close_reason && !['AUTO_BOOKED', 'OVERRIDDEN'].includes(o.automation?.status) &&
    (o.automation?.status?.startsWith('WAIT_') || o.automation?.status==='PAUSED' ||
      ['ACCEPTED', 'SUBMITTED', 'PARTIALLY_CONFIRMED', 'CANCEL_REQUESTED', 'PENDING'].includes(o.status)));
}

export function needsOrderInput(order: Row) {
  return ['WAIT_PAYMENT', 'WAIT_RULES'].includes(order.automation?.status);
}

export function dayCoverageLabel(day?: Row) {
  if (!day || day.status === 'EMPTY') return '暂无持仓';
  if (day.status === 'NOT_OPEN') return '没开盘';
  return day.complete ? `${day.covered_count} / ${day.holding_count} 只已更新` : `${day.covered_count} / ${day.holding_count} 只已更新 · 合计待完整`;
}
