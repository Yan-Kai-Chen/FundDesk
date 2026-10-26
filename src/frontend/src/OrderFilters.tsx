import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Search, RotateCcw } from 'lucide-react';
import type { Row } from './api';
import type { SidebarCatalog } from './sidebarCatalog';
import { DEFAULT_RECORD_FILTERS, filterRecordOrders, hasRecordFilters, readRecordFilters, writeRecordFilters } from './recordFilters';
import type { RecordFilters } from './recordFilters';
import './orderFilters.css';

type Props = {
  mode: string;
  orders: Row[];
  funds: Row[];
  catalog?: SidebarCatalog;
  businessDate: string;
  resetToken?: string | number;
  render: (rows: Row[], filtered: boolean) => ReactNode;
};

function localPreferences() {
  try { return window.localStorage; } catch { return undefined; }
}

// Remounting only this small control on account changes avoids writing the
// previous account's preferences into the next account's storage key.
export function OrderFilters(props: Props) {
  return <FilterControls key={props.mode} {...props}/>;
}

function FilterControls({ mode, orders, funds, catalog, businessDate, resetToken, render }: Props) {
  const [filters, setFilters] = useState<RecordFilters>(() => readRecordFilters(localPreferences(), mode));
  const previousReset = useRef(resetToken);
  useEffect(() => { writeRecordFilters(localPreferences(), mode, filters); }, [filters, mode]);
  useEffect(() => {
    if (previousReset.current !== resetToken) {
      previousReset.current = resetToken;
      setFilters({ ...DEFAULT_RECORD_FILTERS });
    }
  }, [resetToken]);
  useEffect(() => {
    if (filters.fundId && !funds.some(fund => fund.id === filters.fundId))
      setFilters(previous => ({ ...previous, fundId: '' }));
  }, [funds, filters.fundId]);
  const update = <K extends keyof RecordFilters>(key: K, value: RecordFilters[K]) =>
    setFilters(previous => ({ ...previous, [key]: value }));
  const rows = useMemo(() => filterRecordOrders(orders, funds, catalog, filters, businessDate),
    [orders, funds, catalog, filters, businessDate]);
  const filtered = hasRecordFilters(filters);
  return <>
    <div className="order-filterbar" role="search" aria-label="筛选交易记录">
      <label className="order-filter-search"><Search size={17}/><input aria-label="搜索交易" placeholder="基金 / 代码 / 订单号" maxLength={200}
        value={filters.query} onChange={event => update('query', event.target.value)}/></label>
      <div className="order-filter-options">
        <select aria-label="筛选基金" value={filters.fundId} onChange={event => update('fundId', event.target.value)}>
          <option value="">全部基金</option>{funds.map(fund => <option key={fund.id} value={fund.id}>
            {catalog?.funds.find(row => row.fund_id === fund.id)?.display_name || fund.name}
          </option>)}
        </select>
        <select aria-label="筛选买卖方向" value={filters.side} onChange={event => update('side', event.target.value as RecordFilters['side'])}>
          <option value="ALL">全部买卖</option><option value="BUY">买入</option><option value="SELL">卖出</option>
        </select>
        <select aria-label="筛选仓位" value={filters.bucket} onChange={event => update('bucket', event.target.value as RecordFilters['bucket'])}>
          <option value="ALL">全部仓位</option><option value="BASE">底仓</option><option value="TACTICAL">T 仓</option>
        </select>
        <select aria-label="筛选交易进度" value={filters.status} onChange={event => update('status', event.target.value as RecordFilters['status'])}>
          <option value="ALL">全部进度</option><option value="PENDING">待处理</option><option value="BOOKED">已入账</option><option value="CANCELED">已撤单 / 拒绝</option>
        </select>
        <select aria-label="筛选下单日期（北京时间）" value={filters.period} onChange={event => update('period', event.target.value as RecordFilters['period'])}>
          <option value="ALL">全部日期</option><option value="TODAY">今天</option><option value="7D">近 7 天</option><option value="30D">近 30 天</option>
        </select>
      </div>
      <div className="order-filter-count"><span aria-live="polite">{filtered ? `${rows.length} / ${orders.length} 笔` : `共 ${orders.length} 笔`}</span>
        {filtered && <button type="button" className="text-button" onClick={() => setFilters({ ...DEFAULT_RECORD_FILTERS })}><RotateCcw size={15}/>重置</button>}
      </div>
    </div>
    {render(rows, filtered)}
  </>;
}
