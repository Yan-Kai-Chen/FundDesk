import type { Row } from './api';
import { dateLabel } from './api';

// The backend revalues confirmed positions. Historical classification drafts
// keep their own preview basis and must not be overwritten by a live quote.
export function currentBuckets(fund: Row, source: Row | undefined = fund.position_buckets): Row | undefined {
  const valuation = fund.return_valuation;
  if (!valuation || source !== fund.position_buckets) return source;
  return valuation.summary ? {...source, ...valuation.summary} :
    {...source, groups: undefined, notice: '持仓待核对'};
}

export function returnBasisLabel(fund: Row): string {
  const value = fund.return_valuation;
  if (!value) return '正式净值';
  const date = value.date ? dateLabel(value.date, 'Asia/Shanghai') : '';
  const clock = value.is_estimate && value.as_of ?
    dateLabel(value.as_of, 'Asia/Shanghai', true).split(' ').at(-1) : '';
  return [value.label, date, clock, clock ? '北京' : ''].filter(Boolean).join(' · ');
}

export function portfolioReturnLabel(portfolio: Row): string {
  const value = portfolio.return_valuation;
  if (!value) return '正式净值';
  const coverage = value.last_official_count > 0 && value.current_count > 0 ?
    ` · ${value.last_official_count} 只沿用最近正式值` : '';
  return value.label + coverage;
}
