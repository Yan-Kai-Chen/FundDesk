import type { Row } from './api';

const basisFields = ['fund_id', 'units', 'rule_version', 't_profile_version', 'investment_plan_version',
  'quote_id', 'baseline_id', 'market_date'] as const;

// /state and /daily-agenda are independent GETs. A quote/config update can
// change the reference without changing the financial ledger revision. Match
// the immutable input identities before exposing an agenda operation.
export function dailyAgendaMatchesSnapshot(state?: Row | null, agenda?: Row | null): boolean {
  if (!state || !agenda || !state.business_date || state.business_date !== agenda.date ||
      state.revision !== agenda.revision || !Array.isArray(state.funds) || !Array.isArray(agenda.snapshot_basis) ||
      state.funds.length !== agenda.snapshot_basis.length) return false;
  const basis = new Map<string, Row>();
  for (const row of agenda.snapshot_basis) {
    if (!row || typeof row.fund_id !== 'string' || basis.has(row.fund_id) ||
        basisFields.some(field => !Object.prototype.hasOwnProperty.call(row, field))) return false;
    basis.set(row.fund_id, row);
  }
  const seen = new Set<string>();
  return state.funds.every((fund: Row) => {
    if (!fund || typeof fund.id !== 'string' || seen.has(fund.id)) return false;
    seen.add(fund.id);
    const row = basis.get(fund.id);
    if (!row) return false;
    const context = fund.daily_t || {}, today = context.today || {};
    // Values are compared as serialized identities. This performs no financial
    // arithmetic, rounding, quote selection or reference amount calculation.
    return (row.units ?? null) === (fund.units ?? null) &&
      (row.rule_version ?? null) === (fund.rules?._version ?? null) &&
      (row.t_profile_version ?? null) === (context.profile?._version ?? null) &&
      (row.investment_plan_version ?? null) === (fund.investment_plan_profile?._version ?? null) &&
      (row.quote_id ?? null) === (today.quote?.id ?? null) &&
      (row.baseline_id ?? null) === (today.baseline?.id ?? null) &&
      (row.market_date ?? null) === (context.market?.date ?? null);
  });
}
