import type { Row } from './api';

export const researchMethods = [
  { id: 'TREND', label: '趋势跟随', description: '比较月末252交易日总回报与人民币现金参照。' },
  { id: 'VALUE', label: '估值布局', description: '比较指数自身长期估值分位，黄金与主动基金不套用。' },
  { id: 'REBALANCE', label: '仓位再平衡', description: '对照你设置的目标配比，保留底仓保护。' },
];
export const researchStatus: Row = { READY: '资料就绪', OBSERVATION_READY: '观察就绪', WAIT: '等待资料',
  BLOCKED: '条件未满足', PARTIAL: '部分适用', NOT_APPLICABLE: '不适用', REFERENCE_ONLY: '研究参考',
  HOLD: '区间内保持', CHECKING: '更新中', NOT_REQUESTED: '未请求', TARGET_FEASIBLE: '目标可计算', TARGET_ONLY: '配比参考' };
const reasons: Row = {
  MARKET_MAPPING_UNSUPPORTED: '跨市场产品暂不套用此版本', PRODUCT_KIND_NOT_APPLICABLE: '该基金类型不适用',
  TREND_CALENDAR_HISTORY_SHORT: '252交易日窗口未覆盖', TREND_HISTORY_INCOMPLETE: '趋势窗口的总回报尚不完整',
  CASH_BENCHMARK_INCOMPLETE: '人民币现金参照尚不完整', TARGET_WEIGHTS_REQUIRED: '尚未设置目标配比',
  VALUATION_MONTH_HISTORY_INCOMPLETE: '需至少60个完整月的指数估值', PEER_HISTORY_INCOMPLETE_RANKING_PAUSED: '有候选基金缺资料，暂停排名',
  FEES_MISSING: '费用资料待核实', LOT_DATES_UNKNOWN: '批次确认或可卖日期待核实',
  REDEMPTION_FEE_LIMIT: '赎回费超过研究上限', LOT_NOT_REDEEMABLE: '批次尚不可赎回',
  PENDING_APPLICATION: '已有待确认申请', NO_APPLICABLE_INSTRUMENTS: '本组没有适用产品',
  SCENARIO_OUTSIDE_CN_DEALING_SESSION: '当前不在境内盘中情景时段', SAME_DAY_QUOTE_MISSING: '缺少当天报价',
  TARGET_COMPARISON_ONLY: '仅比较目标配比，未生成金额',
  VALUATION_ANCHOR_MISSING: '缺少上个完整月末的指数估值',
  DUPLICATE_EXPOSURE_REQUIRES_MAPPING: '同一暴露有多个产品，需先建立执行映射',
};
export const directionLabel: Row = { INCREASE: '增加', DECREASE: '减少', INCREASE_LARGE: '较大增加', INCREASE_SMALL: '小幅增加', HOLD: '保持', DECREASE_SMALL: '小幅减少', DECREASE_LARGE: '较大减少' };
export const rate = (value: unknown) => value == null ? '—' : `${(Number(value) * 100).toFixed(2)}%`;
export function researchReason(value: string) {
  if (reasons[value]) return reasons[value];
  for (const [code, label] of Object.entries(reasons)) if (value.endsWith(':' + code)) return String(label);
  if (value.includes('MISSING_TOTAL_RETURN_SESSION')) return '总回报日期未完整对齐';
  if (value.includes('PROTECTED_OR_LOCKED_FLOOR')) return '底仓保护或不适用持仓与风险上限冲突';
  if (value.includes('VOLATILITY')) return '当前保护条件下波动预算不可满足';
  return '需补充或核对对应资料';
}
export function selectionMatches(methods: string[], run?: Row | null) {
  return !!run && [...methods].sort().join(',') === [...run.selected_methods].sort().join(',');
}
export function researchItem(run: Row | null | undefined, method: string, fundId: string) {
  return run?.observations?.methods?.[method]?.items?.find((item: Row) => item.fund_id === fundId);
}
