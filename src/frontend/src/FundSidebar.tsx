import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Folder, MoreHorizontal, Plus, Search, Settings2 } from 'lucide-react';
import type { Row } from './api';
import type { SidebarCatalog } from './sidebarCatalog';
import { pct } from './api';
import { fundSections, sidebarFund } from './sidebarCatalog';
import { currentDayDisplay } from './viewData';

export function FundSidebar({ funds, catalog, mode, businessDate, selected, onSelect, onAdd, onManage, overview, onOverview }: {
  funds: Row[]; catalog?: SidebarCatalog; mode: string; businessDate: string;
  overview?: boolean; onOverview?: () => void;
  selected: string; onSelect: (id: string) => void; onAdd: () => void;
  onManage: (target?: { groupId?: string; fundId?: string }) => void;
}) {
  const [query, setQuery] = useState('');
  const storageKey = `funding-sidebar-folds-${mode}`;
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey) || '{}');
      return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    } catch { return {}; }
  });
  const current = funds.find(f => f.id === selected);
  const selectedGroup = current ? sidebarFund(catalog, current).group_id || 'unclassified' : '';
  useEffect(() => {
    if (selectedGroup) setExpanded(old => old[selectedGroup] ? old : { ...old, [selectedGroup]: true });
  }, [selected, selectedGroup]);
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(expanded)); } catch { /* Folding remains usable without browser storage. */ }
  }, [expanded, storageKey]);
  const sections = fundSections(funds, catalog, query);
  const searching = !!query.trim();
  return <aside className="fund-sidebar" aria-label="基金选择">
    <div className="sidebar-heading"><h2>我的基金</h2><div className="sidebar-actions">
      <button className="icon-button" aria-label="管理分类" title="管理分类与显示名称" onClick={() => onManage()}><Settings2 size={18}/></button>
      <button className="icon-button" aria-label="添加基金" onClick={onAdd}><Plus size={20}/></button>
    </div></div>
    {onOverview&&<button className="sidebar-overview" aria-pressed={!!overview} onClick={onOverview}>全部基金 <span>{funds.filter(f=>f.watching||Number(f.units)>0).length}</span></button>}
    <label className="fund-search"><Search size={18}/><input className="fund-search-input" aria-label="搜索基金" placeholder="名称 / 代码 / 标签" value={query} onChange={e => setQuery(e.target.value)}/></label>
    <div className="sidebar-date">{businessDate?.slice(5).replace('-', '/')} · 今日涨跌</div>
    <div className="fund-list">{sections.map(group => {
      const isOpen = searching || !!expanded[group.id];
      return <section className={`fund-section ${selectedGroup === group.id ? 'current' : ''}`} key={group.id}>
        <div className="fund-group-heading">
          <button className="fund-group-toggle" title={group.name} aria-expanded={isOpen} aria-controls={`fund-group-${group.id}`} onClick={() => {
            setQuery(''); setExpanded(old => ({ ...old, [group.id]: !isOpen }));
          }}>
            {isOpen ? <ChevronDown size={15}/> : <ChevronRight size={15}/>}<Folder size={17}/><span>{group.name}</span><span className="group-count">{group.funds.length}</span>
          </button>
          {group.id !== 'unclassified' && <button className="sidebar-edit icon-button" aria-label={`编辑分类 ${group.name}`} onClick={() => onManage({ groupId: group.id })}><MoreHorizontal size={17}/></button>}
        </div>
        {isOpen && <div className="fund-group-items" id={`fund-group-${group.id}`}>
          {group.funds.map(f => {
            const day = currentDayDisplay(f);
            const quote = day.ready ? `${day.official ? '' : '≈'}${pct(day.change)}` : day.label;
            return <div className={`fund-choice-row ${selected === f.id ? 'selected' : ''}`} key={f.id}>
              <button className="fund-choice" aria-pressed={selected === f.id} onClick={() => onSelect(f.id)} title={`${f.name} · ${f.fund_code || 'DEMO'}\n${f.sidebar.theme ? `观察标签：${f.sidebar.theme}\n` : ''}${day.date} ${day.label}${day.asOf ? ` · ${day.asOf}` : ''}`}>
                <strong>{f.sidebar.display_name}</strong>
                <span className="fund-row-meta"><span>{f.sidebar.theme || f.fund_code || 'DEMO'}</span><span>{quote}</span></span>
              </button>
              <button className="sidebar-edit icon-button" aria-label={`编辑基金 ${f.sidebar.display_name}`} onClick={() => onManage({ fundId: f.id })}><MoreHorizontal size={17}/></button>
            </div>;
          })}
          {!group.funds.length && <p className="sidebar-empty">暂无基金</p>}
        </div>}
      </section>;
    })}</div>
    {!sections.length && <p className="sidebar-empty">{funds.some(f => f.watching) ? '未找到基金' : '还没有自选基金'}</p>}
  </aside>;
}
