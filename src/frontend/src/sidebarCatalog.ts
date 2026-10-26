import type { Row } from './api';

export type SidebarGroup = { id: string; name: string };
export type SidebarFund = { fund_id: string; group_id: string | null; display_name: string; theme: string };
export type SidebarCatalog = { groups: SidebarGroup[]; funds: SidebarFund[]; _version: number };
export type FundSection = SidebarGroup & { funds: (Row & { sidebar: SidebarFund })[] };

export function sidebarFund(catalog: SidebarCatalog | undefined, fund: Row): SidebarFund {
  return catalog?.funds.find(f => f.fund_id === fund.id) || {
    fund_id: fund.id, group_id: null, display_name: fund.name, theme: '',
  };
}

export function fundSections(funds: Row[], catalog: SidebarCatalog | undefined, query = ''): FundSection[] {
  const groups = [...(catalog?.groups || []), { id: 'unclassified', name: '未分类' }];
  const term = query.trim().toLocaleLowerCase();
  const rows: (Row & { sidebar: SidebarFund })[] = funds.filter(f => f.watching).map(f => ({ ...f, sidebar: sidebarFund(catalog, f) }));
  return groups.map(group => ({ ...group, funds: rows.filter(f => {
    const groupId = f.sidebar.group_id || 'unclassified';
    return groupId === group.id && (!term ||
      `${group.name} ${f.name} ${f.fund_code || ''} ${f.sidebar.display_name} ${f.sidebar.theme}`.toLocaleLowerCase().includes(term));
  }) })).filter(group => group.funds.length || (!term && group.id !== 'unclassified'));
}

export function removeSidebarGroup(catalog: SidebarCatalog, id: string): SidebarCatalog {
  return { ...catalog, groups: catalog.groups.filter(g => g.id !== id),
    funds: catalog.funds.map(f => f.group_id === id ? { ...f, group_id: null } : f) };
}

export function moveSidebarGroup(catalog: SidebarCatalog, index: number, direction: -1 | 1): SidebarCatalog {
  const target = index + direction;
  if (target < 0 || target >= catalog.groups.length) return catalog;
  const groups = [...catalog.groups];
  [groups[index], groups[target]] = [groups[target], groups[index]];
  return { ...catalog, groups };
}
