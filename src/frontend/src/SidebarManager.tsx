import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Plus, Trash2, X } from 'lucide-react';
import { api, key } from './api';
import type { Row } from './api';
import type { SidebarCatalog, SidebarFund } from './sidebarCatalog';
import { moveSidebarGroup, removeSidebarGroup, sidebarFund } from './sidebarCatalog';

export function SidebarManager({ catalog, funds, target, mode, onClose, onSaved }: {
  catalog: SidebarCatalog; funds: Row[]; target?: { groupId?: string; fundId?: string };
  mode: string; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<SidebarCatalog>(() => ({ ...catalog,
    groups: catalog.groups.map(g => ({ ...g })), funds: funds.map(f => ({ ...sidebarFund(catalog, f) })),
  }));
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const modal = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false), closeRef = useRef(onClose);
  closeRef.current = onClose; busyRef.current = busy;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    if (target?.fundId) {
      const details = document.getElementById(`sidebar-editor-${target.fundId}`) as HTMLDetailsElement | null;
      if (details) details.open = true;
    }
    const focusId = target?.fundId ? `sidebar-alias-${target.fundId}` : target?.groupId ? `sidebar-name-${target.groupId}` : '';
    const focused = focusId ? document.getElementById(focusId) : modal.current?.querySelector<HTMLInputElement>('input');
    focused?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busyRef.current) { e.preventDefault(); closeRef.current(); }
      if (e.key === 'Tab') {
        const controls = Array.from(modal.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input,select,summary') || []).filter(el => el.getClientRects().length);
        const first = controls[0], last = controls.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, [target?.fundId, target?.groupId]);
  const updateFund = (id: string, fields: Partial<SidebarFund>) => setDraft(old => ({ ...old,
    funds: old.funds.map(f => f.fund_id === id ? { ...f, ...fields } : f),
  }));
  return <div className="modal-backdrop"><div className="modal wide" role="dialog" aria-modal="true" aria-labelledby="sidebar-manager-title" ref={modal}>
    <div className="modal-header"><h2 id="sidebar-manager-title">管理我的基金</h2><button className="icon-button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={22}/></button></div>
    <form onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError('');
      try {
        const saved = await api('/fund-sidebar', mode, { groups: draft.groups, funds: draft.funds, expected_version: draft._version }, 'PUT');
        setDraft(old => ({ ...old, _version: saved._version }));
        await onSaved();
      } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
    }}>
      <div className="modal-body sidebar-manager-body"><fieldset disabled={busy}>
        <legend>分类</legend>
        <div className="category-editor">{draft.groups.map((g, index) => <div className="category-editor-row" key={g.id}>
          <input id={`sidebar-name-${g.id}`} aria-label={`分类名称 ${index + 1}`} value={g.name} required maxLength={40} onChange={e => setDraft(old => ({ ...old, groups: old.groups.map(row => row.id === g.id ? { ...row, name: e.target.value } : row) }))}/>
          <button type="button" className="icon-button" aria-label={`上移分类 ${g.name}`} disabled={index === 0} onClick={() => setDraft(old => moveSidebarGroup(old, index, -1))}><ArrowUp size={18}/></button>
          <button type="button" className="icon-button" aria-label={`下移分类 ${g.name}`} disabled={index === draft.groups.length - 1} onClick={() => setDraft(old => moveSidebarGroup(old, index, 1))}><ArrowDown size={18}/></button>
          <button type="button" className="icon-button" aria-label={`删除分类 ${g.name}`} title="基金移至未分类" onClick={() => setDraft(old => removeSidebarGroup(old, g.id))}><Trash2 size={18}/></button>
        </div>)}</div>
        <button className="button" type="button" onClick={() => setDraft(old => ({ ...old, groups: [...old.groups, { id: key(), name: '新分类' }] }))}><Plus size={17}/>新增分类</button>
        <p className="manager-note">删除分类后，基金移至“未分类”。</p>
      </fieldset>
      <fieldset disabled={busy}><legend>基金名称与归属</legend>
        {funds.map(f => {
          const item = draft.funds.find(row => row.fund_id === f.id)!;
          return <details className="sidebar-fund-editor" id={`sidebar-editor-${f.id}`} key={f.id} onInvalid={e => { e.currentTarget.open = true; }}>
            <summary>{item.display_name || f.name}<span>{f.fund_code || 'DEMO'}</span></summary>
            <div className="sidebar-fund-fields">
              <label className="field">显示名称<input id={`sidebar-alias-${f.id}`} value={item.display_name} required maxLength={80} onChange={e => updateFund(f.id, { display_name: e.target.value })}/></label>
              <label className="field">所属分类<select value={item.group_id || ''} onChange={e => updateFund(f.id, { group_id: e.target.value || null })}><option value="">未分类</option>{draft.groups.map(g => <option key={g.id} value={g.id}>{g.name || '未命名分类'}</option>)}</select></label>
              <label className="field">观察标签<input value={item.theme} maxLength={40} placeholder="如 PCB、AI应用" onChange={e => updateFund(f.id, { theme: e.target.value })}/></label>
              <p className="manager-note">基金全名：{f.name}</p>
            </div>
          </details>;
        })}
      </fieldset>
      {error && <div className="error-banner" role="alert">{error}</div>}</div>
      <div className="modal-footer"><button className="button" type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary" type="submit" disabled={busy}>{busy ? '保存中…' : '保存'}</button></div>
    </form>
  </div></div>;
}
