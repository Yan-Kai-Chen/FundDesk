export type Row = Record<string, any>;
let csrf = '';
export async function bootstrap(signal?: AbortSignal) {
  const r = await fetch('/api/v1/session', { credentials: 'same-origin', signal });
  if (!r.ok) throw new Error('无法建立本地会话');
  csrf = (await r.json()).csrf_token;
}
export async function api(path: string, mode: string, body?: Row, method?: string, signal?: AbortSignal) {
  const r = await fetch(`/api/v1${path}${path.includes('?') ? '&' : '?'}mode=${mode}`, {
    method: method || (body ? 'POST' : 'GET'), credentials: 'same-origin', signal,
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: body ? JSON.stringify(body) : undefined,
  });
  const value = await r.json();
  if (!r.ok) throw new Error(value.message || (Array.isArray(value.detail) ? '输入格式不正确，请核对必填项' : value.detail) || '操作失败');
  return value;
}
export const key = () => crypto.randomUUID();
// Conversions here are presentation only; settlement and limits are computed by the backend.
export const cash = (v: unknown) => v == null ? '—' : Number(v).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
export const quantity = (v: unknown, n = 2) => v == null ? '—' : Number(v).toLocaleString('zh-CN', {minimumFractionDigits: n, maximumFractionDigits: n});
export const pct = (v: unknown) => v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${(Number(v) * 100).toFixed(2)}%`;
export function localInput(value: string) {
  const d = new Date(value), pad = (x: number) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function shanghaiInput(value: string) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',
    year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'})
    .formatToParts(new Date(value)).map(p=>[p.type,p.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}
export function submittedTime(value: string) {
  return new Date(`${value}${value.length===16?':00':''}+08:00`).toISOString();
}
export function orderTimeLabel(order: Row) {
  if(order.time_precision==='BEFORE_CUTOFF')return `${dateLabel(order.submitted_at,'Asia/Shanghai')} · 15:00 前`;
  return dateLabel(order.submitted_at,'Asia/Shanghai',order.time_precision!=='DAY');
}
export function dateLabel(value: string, timezone?: string, clock = false) {
  if (!value) return '未记录';
  return new Intl.DateTimeFormat('zh-CN', {timeZone: timezone, month: '2-digit', day: '2-digit',
    ...(clock ? {hour: '2-digit', minute: '2-digit', hour12: false} : {})}).format(new Date(value.length === 10 ? value + 'T04:00:00Z' : value));
}
export const actionLabel: Row = { BUY:'买入计划', SELL:'卖出计划', HOLD:'继续持有', WAIT:'保持观察', BLOCKED:'条件未满足' };
export const statusLabel: Row = { ACCEPTED:'已受理', SUBMITTED:'待确认', PARTIALLY_CONFIRMED:'部分确认', CONFIRMED:'已确认', CANCEL_REQUESTED:'撤单申请中', CANCELED:'已撤销', REJECTED:'已拒绝' };
export const eventLabel: Row = { TRACKING_OPENING:'按现有资料开始跟踪', CASH_DEPOSIT:'资金转入', CASH_WITHDRAWAL:'资金转出', OPENING_LOT:'期初持仓', ORDER_SUBMITTED:'记录申请', ORDER_PAID:'实际扣款', ORDER_CONFIRMED:'渠道确认', ORDER_SETTLED:'赎回到账', ORDER_REFUNDED:'退款到账', ORDER_CANCEL_REQUESTED:'申请撤单', ORDER_CANCELED:'确认撤销', ORDER_REJECTED:'申请被拒绝', REVERSAL:'冲正记录', DIVIDEND_DECLARED:'分红权益', DIVIDEND_PAID:'现金分红到账', DIVIDEND_REINVESTED:'红利再投资', SPLIT:'份额拆分/合并' };
