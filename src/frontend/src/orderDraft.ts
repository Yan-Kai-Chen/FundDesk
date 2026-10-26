// One bounded, local draft per account. Preview results and ledger snapshots are
// deliberately excluded: a restored sale must be priced against current data.
export type OrderDraft = { version: 1; savedAt: number; key: string; fields: Record<string, string | boolean> };
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const TTL = 30 * 24 * 60 * 60 * 1000;
const prefix = 'funding-order-draft-';
const names = new Set(['fund_id', 'side', 'bucket', 'requested', 'submitted_time', 'submitted_date', 'time_precision', 'external_order_id', 'paid_immediately', 'auto_confirm']);
const allowed = (name: string) => names.has(name) || /^lot_[\w-]{1,100}$/.test(name);

export function clearOrderDraft(storage: StorageLike, mode: string) {
  try { storage.removeItem(prefix + mode); } catch { /* The form remains usable without storage. */ }
}

export function readOrderDraft(storage: StorageLike, mode: string, now = Date.now()): OrderDraft | null {
  try {
    const raw = storage.getItem(prefix + mode);
    if (!raw) return null;
    if (raw.length > 24000) throw new Error('Oversized draft');
    const d = JSON.parse(raw);
    if (d.version !== 1 || !Number.isFinite(d.savedAt) || now - d.savedAt > TTL || d.savedAt > now + 60000 ||
        typeof d.key !== 'string' || !d.key || d.key.length > 200 || !d.fields || Array.isArray(d.fields) ||
        typeof d.fields !== 'object' || !['BUY', 'SELL'].includes(d.fields.side) || !['BASE', 'TACTICAL'].includes(d.fields.bucket) ||
        !['EXACT', 'BEFORE_CUTOFF'].includes(d.fields.time_precision) || typeof d.fields.fund_id !== 'string' ||
        Object.entries(d.fields).some(([k, v]) => !allowed(k) || !['string', 'boolean'].includes(typeof v) || String(v).length > 300)) throw new Error('Invalid draft');
    return d;
  } catch { clearOrderDraft(storage, mode); return null; }
}

export function saveOrderDraft(storage: StorageLike, mode: string, fields: Record<string, unknown>, key: string, now = Date.now()): boolean {
  const clean: OrderDraft['fields'] = {};
  for (const [name, value] of Object.entries(fields)) {
    if (allowed(name) && ['string', 'boolean'].includes(typeof value) && String(value).length <= 300) clean[name] = value as string | boolean;
  }
  const draft: OrderDraft = { version: 1, savedAt: now, key, fields: clean };
  const raw = JSON.stringify(draft);
  if (raw.length > 24000) return false;
  try { storage.setItem(prefix + mode, raw); return true; } catch { return false; }
}
