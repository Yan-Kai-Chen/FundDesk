import { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { Row } from './api';
import { cash, quantity } from './api';
import { groupChartTrades } from './chartTrades';
import type { ChartTrade } from './chartTrades';

const EMPTY: any[] = [];
export const Chart = memo(function Chart({ history, trades = EMPTY, openDates = EMPTY, timezone }: {
  history: Row[]; trades?: ChartTrade[]; openDates?: string[]; timezone?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [activeTrade, setActiveTrade] = useState<string | null>(null);
  const gradient = useId();
  const container = useRef<HTMLDivElement>(null);
  const quietAt = useRef(0);
  const dragStart = useRef<{x:number; y:number} | null>(null);
  const dragged = useRef(false);
  const [width, setWidth] = useState(800);
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    let timer: ReturnType<typeof setTimeout>;
    const observer = new ResizeObserver(entries => {
      const next = Math.max(360, entries[0].contentRect.width);
      clearTimeout(timer);
      // CSS stretches the existing SVG during resize; rebuild geometry once settled.
      timer = setTimeout(() => setWidth(next), 120);
    });
    const scrolling = () => { quietAt.current = performance.now() + 200; setHover(null); };
    observer.observe(element);
    window.addEventListener('scroll', scrolling, {capture:true, passive:true});
    window.addEventListener('wheel', scrolling, {passive:true});
    return () => {
      observer.disconnect(); clearTimeout(timer);
      window.removeEventListener('scroll', scrolling, true);
      window.removeEventListener('wheel', scrolling);
    };
  }, [history.length > 0]);
  const pointHover = useCallback((index:number, buttons:number) => {
    if (!buttons && performance.now() >= quietAt.current) setHover(index);
  }, []);
  const activate = useCallback((key:string) => {
    if (dragged.current) return;
    setActiveTrade(old => old === key ? null : key); setHover(null);
  }, []);
  const geometry = useMemo(() => {
    const W = width, H = 316, left = 6, right = 88, top = 26, bottom = 38;
    const values = history.map(x => Number(x.value));
    const min = Math.min(...values), max = Math.max(...values), span = Math.max(max - min, max * .008);
    const low = min - span * .24, high = max + span * .28;
    const intraday = history[0]?.value_kind !== 'OFFICIAL_NAV';
    const dates = history.map(x => new Date(intraday ? x.as_of || x.observed_at : x.effective_date).getTime());
    const duration = Math.max(dates[dates.length - 1] - dates[0], intraday ? 60000 : 86400000);
    const points = history.map((_, i) => [left + (dates[i] - dates[0]) / duration * (W - left - right), top + (high - values[i]) / (high - low) * (H - top - bottom)]);
    const groups = groupChartTrades(history, trades);
    const segments: number[][][] = [[]];
    points.forEach((p, i) => {
      if (i && (dates[i] - dates[i - 1] > (intraday ? 15 * 60000 : 5 * 86400000) ||
        (!intraday && openDates.some(d => d > history[i - 1].effective_date && d < history[i].effective_date)))) segments.push([]);
      segments[segments.length - 1].push(p);
    });
    return {W,H,left,right,top,bottom,high,low,intraday,points,groups,segments};
  }, [history, trades, openDates, width]);
  const {W,H,left,right,top,bottom,high,low,intraday,points,groups,segments} = geometry;
  // Hover only changes the crosshair/readout, not the axes, paths or markers.
  const plot = useMemo(() => <>
    <defs><linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#111" stopOpacity=".05"/><stop offset="100%" stopColor="#111" stopOpacity="0"/></linearGradient></defs>
    {[0, 1, 2, 3].map(i => {
      const y = top + i * (H - top - bottom) / 3;
      return <g key={i}><line x1={left} x2={W - right} y1={y} y2={y} stroke="#e8e8e8" strokeDasharray="3 5"/><text x={W - right + 16} y={y + 5} className="chart-label">{(high - i * (high - low) / 3).toFixed(4)}</text></g>;
    })}
    {segments.map((segment, i) => <g key={i}>{segment.length > 1 && <><path d={`M${segment.map(p => p.join(',')).join(' L')} L${segment.at(-1)![0]},${H - bottom} L${segment[0][0]},${H - bottom} Z`} fill={`url(#${gradient})`}/><polyline points={segment.map(p => p.join(',')).join(' ')} fill="none" stroke="#222" strokeWidth="2.4" strokeLinejoin="round"/></>}{segment.length === 1 && <circle cx={segment[0][0]} cy={segment[0][1]} r="4" fill="#111"/>}</g>)}
    {history.map((r, i) => <g key={r.id}><rect x={Math.max(0, points[i][0] - 7)} y="0" width="15" height={H - bottom} fill="transparent" onPointerMove={e => pointHover(i,e.buttons)}/>{(i === 0 || i === history.length - 1 || i === Math.floor(history.length / 2)) && <text x={points[i][0]} y={H - 5} textAnchor={i === 0 ? 'start' : i === history.length - 1 ? 'end' : 'middle'} className="chart-label">{intraday ? new Intl.DateTimeFormat('zh-CN', { timeZone: timezone, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(r.as_of || r.observed_at)) : r.effective_date.slice(5).replace('-', '/')}</text>}</g>)}
    {groups.map(g => {
      const key = `${g.date}-${g.side}`, [x, y] = points[g.index];
      const isBuy = g.side === 'BUY';
      const bothSides = groups.some(other => other.index === g.index && other.side !== g.side);
      const markerX = x + (bothSides ? isBuy ? -7 : 7 : 0);
      return <g key={key} className="trade-marker" role="button" tabIndex={0}
        aria-label={`${g.date} ${isBuy ? '买入' : '卖出'} · ${g.trades.length} 笔`}
        onPointerMove={e => pointHover(g.index,e.buttons)} onFocus={() => setHover(g.index)}
        onClick={() => activate(key)} onKeyDown={e => { if (['Enter', ' '].includes(e.key)) { e.preventDefault(); dragged.current = false; activate(key); } }}>
        <title>{`${g.date} ${isBuy ? '买入' : '卖出'} ${g.trades.length} 笔，点击查看`}</title>
        <circle cx={markerX} cy={y} r="12" fill="transparent"/>
        {isBuy ? <circle cx={markerX} cy={y} r="5.5" fill="#7ba5dd" stroke="#fff" strokeWidth="2"/> : <rect x={markerX - 5} y={y - 5} width="10" height="10" rx="1.5" fill="#c6b587" stroke="#fff" strokeWidth="2"/>}
      </g>;
    })}
  </>, [geometry, gradient, history, timezone, pointHover, activate]);
  const selected = groups.find(g => `${g.date}-${g.side}` === activeTrade);
  const selectedIndex = hover != null && history[hover] ? hover : selected?.index;
  if (!history.length) return <div className="chart-empty">暂无记录</div>;
  return <div className="chart-container" ref={container} onMouseLeave={() => setHover(null)}
    onPointerDown={e => { dragStart.current = {x:e.clientX,y:e.clientY}; dragged.current = false; }}
    onPointerMove={e => { if (e.buttons && dragStart.current && Math.hypot(e.clientX-dragStart.current.x,e.clientY-dragStart.current.y)>4) { dragged.current = true; setHover(null); } }}
    onPointerUp={() => { dragStart.current = null; }} onPointerCancel={() => { dragStart.current = null; dragged.current = true; }}
    onKeyDown={e => { if (e.key === 'Escape') { setActiveTrade(null); setHover(null); } }}>
    <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label={intraday?'分时估值走势':'净值走势与买卖记录'}>
      {plot}
      {selectedIndex != null && <line x1={points[selectedIndex][0]} x2={points[selectedIndex][0]} y1={top} y2={H - bottom} stroke="#888" strokeDasharray="3 4" pointerEvents="none"/>}
    </svg>
    {groups.length > 0 && <div className="chart-legend"><span><i className="buy-dot"/>买入</span><span><i className="sell-dot"/>卖出</span></div>}
    <div className="chart-readout">{selectedIndex != null ? `${history[selectedIndex].effective_date} · 净值 ${quantity(history[selectedIndex].value,4)}` : '\u00a0'}</div>
    {selected && <div className="chart-tooltip">
      <button className="text-button chart-close" aria-label="关闭交易明细" onClick={() => setActiveTrade(null)}>收起</button>
      <div>{selected.date} · 净值 {quantity(history[selected.index].value, 4)}</div>
      <strong>{selected.side === 'BUY' ? '买入' : '卖出'}{selected.trades.length > 1 ? ` · ${selected.trades.length} 笔` : ''}</strong>{selected.trades.map(t => <div className="trade-tooltip-row" key={t.id}>{t.amount != null && <span>¥{cash(t.amount)}</span>}{t.units != null && <span>{quantity(t.units)} 份</span>}</div>)}<small>{selected.trades[0].basis}</small>
    </div>}
  </div>;
});
