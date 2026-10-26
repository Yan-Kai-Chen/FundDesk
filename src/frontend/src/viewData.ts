type Data = Record<string, any>;

// Use the backend's current-day selection everywhere, never yesterday's move.
export function currentDayDisplay(fund?: Data) {
  const today = fund?.daily_t?.today;
  const ready = today?.status === 'READY' && today.quote != null;
  const official = ready && today.quote.value_kind === 'OFFICIAL_NAV';
  return {
    ready, official, date: fund?.daily_t?.market?.date || '',
    change: ready ? today.change : null,
    value: ready ? today.quote.value : null,
    label: ready ? (official ? '当日正式' : today.phase === 'CLOSE' ? '收盘估算' : today.phase === 'BREAK' ? '午间休市估算' : '盘中估算') : today?.label || '待更新',
    valueLabel: official ? '正式净值' : '估算净值',
    asOf: ready ? today.quote.as_of : null,
  };
}

// Reuse unchanged subtrees so a new quote does not rebuild the formal NAV SVG,
// and a fresh server clock alone does not repaint the whole workbench.
export function shareData<T>(previous: T | undefined | null, next: T): T {
  if (Object.is(previous, next)) return previous as T;
  if (!previous || !next || typeof previous !== 'object' || typeof next !== 'object' ||
      Array.isArray(previous) !== Array.isArray(next)) return next;
  const old = previous as Data, value = next as Data;
  const keys = Object.keys(value);
  let same = Object.keys(old).length === keys.length;
  const result: Data = Array.isArray(next) ? [] : {};
  for (const key of keys) {
    result[key] = shareData(old[key], value[key]);
    if (!Object.prototype.hasOwnProperty.call(old, key) || result[key] !== old[key]) same = false;
  }
  return (same ? previous : result) as T;
}

export function shareSnapshot(previous: Data | null, next: Data): Data {
  if (!previous) return next;
  const shared = shareData(previous, { ...next, now: previous.now });
  return shared === previous ? previous : { ...shared, now: next.now };
}

export function displayPlan(plan: Data | null): Data | null {
  // UI reference only. Executable source-plan IDs are not projected here.
  return plan ? { items: plan.items, daily_t_items: plan.daily_t_items } : null;
}

export function investmentPlanMatchesFund(fund: Data, reference?: Data, asOf?: string): boolean {
  const profile = fund.investment_plan_profile;
  if (!profile?.enabled || reference?.level !== 'PLAN' || reference.fund_id !== fund.id ||
      reference.profile_version !== profile._version) return false;
  const template = profile.template || 'DAILY_LADDER_V1';
  if ((reference.plan?.template || 'DAILY_LADDER_V1') !== template) return false;
  if (template !== 'DRAWDOWN_GUARD_V2') return true;
  const risk = reference.plan?.risk, basis = risk?.input_basis;
  if (!risk || !basis || !Array.isArray(risk.input_ids) || !Array.isArray(fund.history)) return false;
  // A blocked data-quality result may return before choosing historical inputs.
  // Keep its explanation visible; it can never prepare an operation.
  if (risk.ready === false && reference.can_apply !== true && basis.history_digest == null) return true;
  const today = fund.daily_t?.market?.date;
  if (!today || (asOf && !Number.isFinite(Date.parse(asOf)))) return false;
  const eligible = fund.history.filter((row: Data) => row.value_kind === 'OFFICIAL_NAV' && row.effective_date <= today &&
    (!asOf || (Date.parse(row.observed_at) <= Date.parse(asOf) &&
      (!row.as_of || Date.parse(row.as_of) <= Date.parse(asOf)) &&
      (!row.published_at || Date.parse(row.published_at) <= Date.parse(asOf))))).sort((a: Data, b: Data) => {
        for (const key of ['effective_date', 'observed_at', 'id']) {
          if (a[key] !== b[key]) return a[key] < b[key] ? -1 : 1;
        }
        return 0;
      });
  const history: Data[] = Array.from(new Map(eligible.map((row: Data) => [row.effective_date, row])).values()).slice(-181) as Data[];
  return basis.history_count === history.length && (basis.history_end_date ?? null) === (history.at(-1)?.effective_date ?? null) &&
    risk.input_ids.length === history.length && risk.input_ids.every((id: string, index: number) => id === history[index].id) &&
    (!basis.quote_id || basis.quote_id === fund.daily_t?.today?.quote?.id);
}

export function planMatchesSnapshot(state: Data, plan: Data): boolean {
  if (state.mode !== plan.mode || state.revision !== plan.ledger_version) return false;
  return (plan.daily_t_items || []).every((item: Data) => {
    const f = state.funds.find((fund: Data) => fund.id === item.fund_id);
    const profile = item.level === 'PLAN' ? f?.investment_plan_profile : f?.daily_t?.profile;
    const correctKind = item.level === 'PLAN' ? profile?.enabled === true : !f?.investment_plan_profile?.enabled;
    return f && correctKind && item.profile_version === profile?._version &&
      (item.level !== 'PLAN' || investmentPlanMatchesFund(f, item, state.now)) &&
      (item.observation_id ?? null) === (f.daily_t.today.quote?.id ?? null) &&
      (item.baseline_id ?? null) === (f.daily_t.today.baseline?.id ?? null);
  }) && (plan.items || []).every((item: Data) => {
    const f = state.funds.find((fund: Data) => fund.id === item.fund_id);
    return f && item.config_version === f.strategy._version && item.rule_version === f.rules._version &&
      (!item.observation_id || item.observation_id === (f.estimate || f.nav)?.id);
  });
}

// This only prepares the local recording form. Financial amounts, unit rounding,
// quote freshness and plan limits are decided by the backend reference.
export function investmentPlanRecord(fund: Data, reference?: Data): Data | null {
  if (!investmentPlanMatchesFund(fund, reference) || reference?.can_apply !== true || reference.preview_only === true)
    return null;
  const requested = reference.action === 'BUY' ? reference.amount : reference.action === 'SELL' ? reference.units : null;
  if (requested == null || !Number.isFinite(Number(requested)) || Number(requested) <= 0) return null;
  return {fund_id: fund.id, side: reference.action, bucket: 'TACTICAL', requested: String(requested)};
}
