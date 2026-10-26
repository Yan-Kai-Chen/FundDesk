type Data = Record<string, any>;
export type ChartTrade = {
  id: string; date: string; side: 'BUY' | 'SELL'; amount?: unknown;
  units?: unknown; basis: string;
};
export type TradeGroup = { date: string; side: 'BUY' | 'SELL'; index: number; trades: ChartTrade[] };

// Screenshot times retain their Beijing calendar date. New trades use the
// channel's confirmed NAV date, never the application or settlement timestamp.
export function collectChartTrades(fund: Data, orders: Data[]): ChartTrade[] {
  const historical = fund.tracking?.historical_rows;
  const trades: ChartTrade[] = historical
    ? historical.map((r: Data) => ({ id: r.id, date: r.at.slice(0, 10), side: r.side,
        amount: r.side === 'BUY' ? r.cost : r.net, units: r.units, basis: '历史还原 · 按原记录日期' }))
    : (fund.statement?.rows || []).filter((r: Data) => !['CLOSED', 'CANCELED', 'REJECTED'].includes(r.status))
        .map((r: Data) => ({ id: r.id, date: r.displayed_at.slice(0, 10), side: r.side,
          amount: r.unit === 'CNY' ? r.value : undefined,
          units: ['FUND_UNIT', 'SHARES'].includes(r.unit) ? r.value : undefined,
          basis: '截图记录 · 非渠道确认净值日' }));
  for (const order of orders.filter(o => o.fund_id === fund.id)) {
    for (const c of order.confirmations || []) {
      trades.push({ id: c.event_id, date: c.nav_date, side: order.side,
        amount: order.side === 'BUY' ? c.gross : c.net, units: c.units,
        basis: c.confirmation_basis === 'AUTO_OFFICIAL_NAV'
          ? '正式净值自动计算 · 按申请净值日' : '渠道确认 · 按确认净值日' });
    }
  }
  return trades.filter(t => t.date && ['BUY', 'SELL'].includes(t.side));
}

export function groupChartTrades(history: Data[], trades: ChartTrade[]): TradeGroup[] {
  if (!history.length || history.some(h => h.value_kind !== 'OFFICIAL_NAV')) return [];
  const dates = new Map(history.map((h, index) => [h.effective_date, index]));
  const seen = new Set<string>(), groups = new Map<string, TradeGroup>();
  for (const trade of trades) {
    const index = dates.get(trade.date);
    if (index === undefined || seen.has(trade.id)) continue;
    seen.add(trade.id);
    const key = `${trade.date}-${trade.side}`;
    if (!groups.has(key)) groups.set(key, { date: trade.date, side: trade.side, index, trades: [] });
    groups.get(key)!.trades.push(trade);
  }
  return [...groups.values()].sort((a, b) => a.date.localeCompare(b.date) || a.side.localeCompare(b.side));
}
