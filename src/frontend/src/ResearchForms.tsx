import { useEffect, useRef, useState } from 'react';
import { FileInput, X } from 'lucide-react';
import type { Row } from './api';
import { api } from './api';
import { rate } from './researchView';
import { sidebarFund } from './sidebarCatalog';

function useResearchDialog(onClose: () => void, busy: boolean) {
  const modal = useRef<HTMLDivElement>(null), current = useRef({ onClose, busy });
  current.current = { onClose, busy };
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    modal.current?.querySelector<HTMLElement>('input,select,textarea,button')?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !current.current.busy) { event.preventDefault(); current.current.onClose(); }
      if (event.key !== 'Tab') return;
      const controls = Array.from(modal.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select,textarea,summary') || []).filter(el => el.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, []);
  return modal;
}

export function ResearchPolicyForm({ state, research, mode, onClose, onSaved }: {
  state: Row; research: Row; mode: string; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const policy = research.policy;
  const [level, setLevel] = useState(policy.level);
  const [targets, setTargets] = useState<Row>(() => Object.fromEntries(state.funds.map((f: Row) => [f.id, policy.target_weights[f.id] == null ? '' : String(Number(policy.target_weights[f.id]) * 100)])));
  const [budget, setBudget] = useState(policy.cash_budget ?? '');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const modal = useResearchDialog(onClose, busy);
  return <div className="modal-backdrop"><div className="modal wide" role="dialog" aria-modal="true" aria-labelledby="research-policy-title" ref={modal}>
    <div className="modal-header"><h2 id="research-policy-title">研究目标与强度</h2><button className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={22}/></button></div>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError('');
      try {
        const percentages = Object.fromEntries(Object.entries(targets).filter(([, v]) => v !== '').map(([id, value]) => [id, String(value)]));
        await api('/research/policy', mode, { level, target_percentages: percentages, cash_budget: budget === '' ? null : budget, expected_version: policy._version }, 'PUT');
        await onSaved();
      } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
    }}>
      <div className="modal-body"><fieldset className="research-levels" disabled={busy}><legend>研究强度</legend>{['OBSERVE','CAUTIOUS','BALANCED','ACTIVE'].map(id => { const row = research.levels[id]; return <label key={id} className={level === id ? 'chosen' : ''}><input type="radio" name="research-level" checked={level === id} onChange={() => setLevel(id)}/><strong>{row.label}</strong><span>{row.volatility_limit ? `年化波动上限 ${rate(row.volatility_limit)} · 缓冲 ${rate(row.band)}` : '先看观察，不生成金额方案'}</span></label>; })}</fieldset>
      <p className="research-note">档位是未优化的研究约束，与每日做 T 强度分开。选择档位不改变持仓，也不执行交易。</p>
      <fieldset className="research-targets" disabled={busy}><legend>目标配比 · 已登记基金范围</legend>{state.funds.map((f: Row) => <label className="research-target-row" key={f.id}><span>{sidebarFund(state.fund_sidebar, f).display_name}</span><input aria-label={`${f.fund_code || f.name} 目标百分比`} type="number" min="0" max="100" step="0.01" placeholder="未设置" value={targets[f.id]} onChange={e => setTargets(old => ({ ...old, [f.id]: e.target.value }))}/><span>%</span></label>)}</fieldset>
      <p className="research-note">配比由你决定；留空表示尚未设置，合计最多100%。现有底仓保持保护。</p>
      <label className="field">额外研究预算（元，可留空）<input type="number" min="0" step="0.01" value={budget} onChange={e => setBudget(e.target.value)} placeholder="不维护账户总金额"/></label>
      <p className="research-note">预算只用于研究情景，不记入账户资金。填0表示此研究没有额外买入资金；留空时暂停完整金额方案。</p>
      {error && <div className="error-banner" role="alert">{error}</div>}</div>
      <div className="modal-footer"><button className="button" type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary" type="submit" disabled={busy}>{busy ? '保存中…' : '保存研究设置'}</button></div>
    </form>
  </div></div>;
}

export function ResearchInputForm({ research, mode, onClose, onSaved }: {
  research: Row; mode: string; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const [text, setText] = useState(''), [preview, setPreview] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const modal = useResearchDialog(onClose, busy);
  const read = () => JSON.parse(text);
  return <div className="modal-backdrop"><div className="modal wide" role="dialog" aria-modal="true" aria-labelledby="research-input-title" ref={modal}>
    <div className="modal-header"><h2 id="research-input-title">导入研究快照</h2><button className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={22}/></button></div>
    <form onSubmit={async event => {
      event.preventDefault(); if (!preview) return; setBusy(true); setError('');
      try { await api('/research/snapshots', mode, { snapshot: read(), preview_hash: preview.raw_hash, expected_version: research.snapshot_import?.version || 0 }); await onSaved(); }
      catch (err) { setError((err as Error).message); } finally { setBusy(false); }
    }}>
      <div className="modal-body"><p>由 Codex 整理的完整研究快照，包含来源、总回报、估值、日历、批次与研究参数。导入资料不会改变真实持仓。</p><p><a className="text-button" href={`/api/v1/research/contracts/input?mode=${mode}`} download>下载资料格式</a></p>
      <label className="button research-file-input"><FileInput size={18}/>选择 JSON 文件<input type="file" accept=".json,application/json" disabled={busy} onChange={async e => {
        const file = e.target.files?.[0]; if (!file) return; setPreview(null); setError('');
        if (file.size > 2100000) { setError('研究快照文件应小于2.1MB'); return; }
        setText(await file.text());
      }}/></label>
      <label className="field">快照内容<textarea rows={10} value={text} onChange={e => { setText(e.target.value); setPreview(null); }} placeholder="也可以粘贴标准研究快照" disabled={busy}/></label>
      <button className="button" type="button" disabled={busy || !text.trim()} onClick={async () => {
        setBusy(true); setError(''); setPreview(null);
        try { setPreview(await api('/research/snapshots/preview', mode, { snapshot: read() })); }
        catch (err) { setError((err as Error).message); } finally { setBusy(false); }
      }}>核对快照</button>
      {preview && <div className="order-summary"><strong>格式与计算合同通过</strong><span>快照时间 {preview.as_of}</span><span>已验证 {Object.keys(preview.methods).length} 种方法；计算就绪不代表投资效果已验证。</span></div>}
      {error && <div className="error-banner" role="alert">{error}</div>}</div>
      <div className="modal-footer"><button className="button" type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary" type="submit" disabled={busy || !preview}>{busy ? '处理中…' : '保存研究快照'}</button></div>
    </form>
  </div></div>;
}
