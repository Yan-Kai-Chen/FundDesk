import { startTransition, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownLeft, ArrowUpRight, ArrowRight, Check, Database, Download, FileInput, FlaskConical, Leaf, Plus, RefreshCw, Search, Settings2, ShieldCheck, Wallet, X, LayoutDashboard, History, FolderOpen } from 'lucide-react';
import type { Row } from './api';
import { api, bootstrap, cash, quantity, pct, dateLabel, orderTimeLabel, actionLabel, statusLabel, eventLabel } from './api';
import { Chart } from './Chart';
import { OverviewDesk } from './OverviewDesk';
import { OrderFilters } from './OrderFilters';
import { readOrderDraft, clearOrderDraft } from './orderDraft';
import { collectChartTrades } from './chartTrades';
import { Forms } from './Forms';
import { StatementPanel } from './StatementPanel';
import { PositionBuckets } from './PositionBuckets';
import { TrackingSummary, TrackingHistory, FundHoldings } from './TrackingDesk';
import { FundSidebar } from './FundSidebar';
import { SidebarManager } from './SidebarManager';
import { sidebarFund } from './sidebarCatalog';
import { ResearchWorkspace } from './ResearchWorkspace';
import { ResearchInputForm, ResearchPolicyForm } from './ResearchForms';
import { DailyT, TProfileForm } from './DailyT';
import { InvestmentPlanForm } from './InvestmentPlan';
import { StoragePanel } from './StoragePanel';
import { RefreshCoordinator } from './refreshCoordinator';
import { currentDayDisplay, displayPlan, planMatchesSnapshot, shareData, shareSnapshot } from './viewData';
import { ReconciliationPanel } from './ReconciliationPanel';
import { TReviewPanel } from './TReviewPanel';
import { MaintenancePanel } from './MaintenancePanel';
import { restoredPage, rememberPage } from './sessionView';
import { dailyAgendaMatchesSnapshot } from './dailyAgendaData';
import { DEFAULT_RECORD_FILTERS, writeRecordFilters } from './recordFilters';
import { NumberExplanation } from './NumberExplanation';
import { portfolioRiskMatchesSnapshot } from './PortfolioRisk';
import { PlanScenarios } from './PlanScenarios';

const EMPTY: any[] = [];

const entry = new URLSearchParams(window.location.search);
const pages = [{id:'workspace',label:'工作台',icon:LayoutDashboard},{id:'records',label:'交易记录',icon:History},{id:'research',label:'预测研究',icon:FlaskConical},{id:'data',label:'数据管理',icon:Database},{id:'settings',label:'设置',icon:Settings2}];
const typeLabel: Row = {EQUITY_FUND:'权益基金',GOLD_LINKED:'黄金联接',BOND_FUND:'债券基金',MIXED_FUND:'偏股混合',OPEN_END_FUND:'场外基金'};

export default function App() {
  const [mode,setMode] = useState(['real','demo'].includes(entry.get('mode')||'')?entry.get('mode')!:localStorage.getItem('funding-mode') || 'demo');
  const [page,setPage] = useState(()=>restoredPage(localStorage,mode,!!entry.get('fund')));
  const [state,setState] = useState<Row|null>(null), [plan,setPlan] = useState<Row|null>(null);
  const [selected,setSelected] = useState('');
  const [overview,setOverview] = useState(()=>!entry.get('fund')&&localStorage.getItem('funding-workspace-'+mode)!=='detail');
  const [refreshingAll,setRefreshingAll] = useState(false);
  const [hasDraft,setHasDraft] = useState(false), [ordersResetToken,setOrdersResetToken] = useState(0);
  const [period,setPeriod] = useState('3m'), [series,setSeries] = useState('nav');
  const [dialog,setDialog] = useState<Row|null>(null), [notification,setNotification] = useState('');
  const [error,setError] = useState(''), [busy,setBusy] = useState(false);
  const [predictions,setPredictions] = useState<Row[]>([]), [evaluation,setEvaluation] = useState<Row|null>(null), [backups,setBackups] = useState<Row[]>([]);
  const [research,setResearch] = useState<Row|null>(null);
  const [forecasting,setForecasting] = useState<Row|null>(null);
  const [forecastScorecard,setForecastScorecard] = useState<Row|null>(null);
  const [maintenance,setMaintenance] = useState<Row|null>(null);
  const [timezone,setTimezone] = useState(localStorage.getItem('funding-tz') || 'system');
  const [recordTab,setRecordTab] = useState('orders');
  const [importKind,setImportKind] = useState('observations'), [csv,setCsv] = useState(''), [preview,setPreview] = useState<Row|null>(null);
  const tz = timezone === 'system' ? Intl.DateTimeFormat().resolvedOptions().timeZone : timezone;
  const toast = (message: string) => { setNotification(message); window.setTimeout(()=>setNotification(''),4500); };

  const context = useRef({page, selected, dialog, mode, overview});
  context.current = {page, selected, dialog, mode, overview};
  const refresh = useRef<RefreshCoordinator<Row> | null>(null);
  const load = useCallback(() => refresh.current?.refreshNow() || Promise.resolve(), []);
  useEffect(() => {
    setState(null); setPlan(null); setPreview(null); setEvaluation(null); setResearch(null); setForecasting(null); setForecastScorecard(null); setMaintenance(null); setError('');
    let ready = false;
    const coordinator = new RefreshCoordinator<Row>({
      fetch: async signal => {
        if (!ready) { await bootstrap(signal); ready = true; }
        const view = {...context.current};
        let data = await api('/state', mode, undefined, undefined, signal);
        const plans = await api('/recommendations/current', mode, undefined, undefined, signal);
        // A quote or ledger write may land between the two requests. Never mix
        // a reference amount with a different position/quote snapshot.
        if (!planMatchesSnapshot(data, plans)) data = await api('/state', mode, undefined, undefined, signal);
        const extra: Row = {};
        if (view.page === 'workspace' && view.overview) {
          const [agenda,risk] = await Promise.all([api('/daily-agenda', mode, undefined, undefined, signal),api('/portfolio-risk', mode, undefined, undefined, signal)]);
          if (!dailyAgendaMatchesSnapshot(data, agenda)||!portfolioRiskMatchesSnapshot(data,risk)) data = await api('/state', mode, undefined, undefined, signal);
          if (dailyAgendaMatchesSnapshot(data, agenda)) data={...data,daily_agenda:agenda};
          if (portfolioRiskMatchesSnapshot(data,risk)) data={...data,portfolio_risk:risk};
        }
        if (view.page === 'settings') {
          extra.backups = await api('/backups', mode, undefined, undefined, signal);
          extra.maintenance = await api('/maintenance', mode, undefined, undefined, signal);
        }
        if (view.page === 'research') {
          extra.predictions = await api('/predictions', mode, undefined, undefined, signal);
          extra.research = await api('/research', mode, undefined, undefined, signal);
          extra.forecasting = await api('/forecasting', mode, undefined, undefined, signal);
          extra.forecastScorecard = await api('/forecasting/scorecard', mode, undefined, undefined, signal);
          if (view.selected) extra.evaluation = await api(`/predictions/evaluate/${view.selected}`, mode, undefined, undefined, signal).catch(e => ({status:'WAIT',notice:e.message}));
        }
        return {data, plans: planMatchesSnapshot(data, plans) ? displayPlan(plans) : null, view, extra};
      },
      commit: ({data, plans, view, extra}) => startTransition(() => {
        setState(old => shareSnapshot(old, data));
        setPlan(old => shareData(old, plans));
        setSelected(old => data.funds.some((f:Row) => f.id === old) ? old : data.funds.find((f:Row) => entry.get('fund') && f.fund_code === entry.get('fund'))?.id || data.funds.find((f:Row) => f.id === (localStorage.getItem('funding-selected-' + mode) || 'demo-equity'))?.id || data.funds[0]?.id || '');
        if (view.page === context.current.page) {
          if (extra.backups) setBackups(old => shareData(old, extra.backups));
          if (extra.maintenance) setMaintenance(old => shareData(old, extra.maintenance));
          if (extra.predictions) setPredictions(old => shareData(old, extra.predictions));
          if (extra.research) setResearch(old => shareData(old, extra.research));
          if (extra.forecasting) setForecasting(old => shareData(old, extra.forecasting));
          if (extra.forecastScorecard) setForecastScorecard(old => shareData(old, extra.forecastScorecard));
          if (extra.evaluation && view.selected === context.current.selected) setEvaluation(old => shareData(old, extra.evaluation));
        }
        setError('');
      }),
      error: e => setError(e.message),
      canCommit: () => document.visibilityState === 'visible' && !context.current.dialog &&
        !document.activeElement?.matches('input, textarea, select, [contenteditable="true"]'),
    });
    refresh.current = coordinator;
    const pointers = new Set<number>();
    const down = (e:PointerEvent) => { pointers.add(e.pointerId); coordinator.hold(true); };
    const up = (e:PointerEvent) => { pointers.delete(e.pointerId); coordinator.hold(pointers.size > 0); };
    const activity = () => { coordinator.activity(); queueMicrotask(() => coordinator.wake()); };
    const moving = (e:PointerEvent) => { if (e.buttons) coordinator.activity(); };
    const resume = () => { coordinator.activity(); coordinator.wake(); };
    const blur = () => { pointers.clear(); coordinator.hold(false); };
    const events = ['wheel', 'scroll', 'resize', 'keydown', 'input', 'focusin', 'focusout'] as const;
    events.forEach(name => window.addEventListener(name, activity, {capture:true, passive:true}));
    window.addEventListener('pointerdown', down, true);
    window.addEventListener('pointerup', up, true);
    window.addEventListener('pointercancel', up, true);
    window.addEventListener('pointermove', moving, {passive:true});
    window.addEventListener('focus', resume);
    window.addEventListener('blur', blur);
    document.addEventListener('visibilitychange', resume);
    void coordinator.refreshNow().catch(() => {});
    return () => {
      coordinator.dispose();
      if (refresh.current === coordinator) refresh.current = null;
      events.forEach(name => window.removeEventListener(name, activity, true));
      window.removeEventListener('pointerdown', down, true);
      window.removeEventListener('pointerup', up, true);
      window.removeEventListener('pointercancel', up, true);
      window.removeEventListener('pointermove', moving);
      window.removeEventListener('focus', resume);
      window.removeEventListener('blur', blur);
      document.removeEventListener('visibilitychange', resume);
    };
  }, [mode]);
  useEffect(()=>rememberPage(localStorage,mode,page),[mode,page]);
  useEffect(() => { refresh.current?.wake(); }, [dialog]);
  useEffect(() => {
    setEvaluation(null);
    if (page === 'research' || page === 'settings' || (page==='workspace'&&overview)) void load().catch(() => {});
  }, [page, selected, mode, overview, load]);
  const fund: Row|undefined = state?.funds.find((f:Row)=>f.id===selected);
  const fundDay = currentDayDisplay(fund);
  const fundPresentation = fund ? sidebarFund(state?.fund_sidebar, fund) : null;
  useEffect(()=>{if(selected)localStorage.setItem('funding-selected-'+mode,selected);},[selected,mode]);
  const recommendation = plan?.items.find((r:Row)=>r.fund_id===selected);
  const funds: Row[] = state?.funds||[];
  const open = (type:string,target?:Row)=>{
    const draft=type==='order'?readOrderDraft(localStorage,mode):null;
    const resume=!!draft&&funds.some(f=>f.id===draft.fields.fund_id);
    setDialog({type,target,fundId:selected,resume});
    if(resume)toast('已继续未完成交易；如需新建，请先放弃这份草稿');
  };
  const selectFund=(id:string)=>{setSelected(id);setOverview(false);setSeries('nav');localStorage.setItem('funding-workspace-'+mode,'detail');};
  const showOverview=()=>{setOverview(true);localStorage.setItem('funding-workspace-'+mode,'overview');};
  useEffect(()=>{setOverview(!entry.get('fund')&&localStorage.getItem('funding-workspace-'+mode)!=='detail');},[mode]);
  useEffect(()=>{
    if(dialog||!state||state.mode!==mode)return;
    const draft=readOrderDraft(localStorage,mode);
    if(draft&&!state.funds.some((f:Row)=>f.id===draft.fields.fund_id)){clearOrderDraft(localStorage,mode);setHasDraft(false);}
    else setHasDraft(!!draft);
  },[dialog,mode,state?.funds]);
  const refreshAll=async()=>{
    const requestMode=mode;setRefreshingAll(true);
    try{const result=await api('/market/refresh-all',mode,{});await load();
      if(context.current.mode===requestMode)toast(`完成 ${result.completed_count} · 等待 ${result.waiting_count} · 失败 ${result.failed_count}${result.unsupported_count?` · 暂不支持 ${result.unsupported_count}`:''}`);
    }catch(e){if(context.current.mode===requestMode)toast((e as Error).message);}finally{setRefreshingAll(false);}
  };
  const fundName = (id:string)=>funds.find(f=>f.id===id)?.name||'未登记基金';
  const perform = async(operation:()=>Promise<unknown>,message:string)=>{setBusy(true);try{await operation();await load();toast(message);}catch(e){toast((e as Error).message);}finally{setBusy(false);}};
  useEffect(()=>{
    const onKey=(e:KeyboardEvent)=>{if((e.metaKey||e.ctrlKey)&&e.key==='k'&&page==='workspace'&&!dialog){e.preventDefault();document.querySelector<HTMLInputElement>('.fund-search-input')?.focus();return;}if(['INPUT','TEXTAREA','SELECT'].includes((e.target as HTMLElement).tagName)||dialog||page!=='workspace')return;
      if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const list=funds.filter(f=>f.watching);const i=list.findIndex(f=>f.id===selected);selectFund(list[(i+(e.key==='ArrowDown'?1:-1)+list.length)%list.length]?.id||'');}};
    window.addEventListener('keydown',onKey);return()=>window.removeEventListener('keydown',onKey);
  },[funds,selected,page,dialog]);

  const history:Row[] = (series === 'nav' ? fund?.history : fund?.estimate_history) || EMPTY;
  const chartHistory = useMemo(() => period === '1m' ? history.slice(-22) : period === '3m' ? history.slice(-66) : history, [history, period]);
  const chartTrades = useMemo(() => fund ? collectChartTrades(fund, state?.orders || EMPTY) : EMPTY, [fund?.id, fund?.tracking?.historical_rows, fund?.statement?.rows, state?.orders]);

  if (!state || state.mode !== mode) return <div className="boot"><Leaf size={36}/><h1>FundDesk</h1><p>{error||'正在打开本地基金工作台…'}</p>{error&&<button className="primary" onClick={()=>void load().catch(() => {})}>重新连接</button>}</div>;
  const portfolio=state.portfolio;
  const renderOrders = (orders:Row[],compact=false)=><div className="table-wrap"><table><thead><tr><th>基金 / 下单（北京）</th><th>方向</th><th>申请金额 / 份额</th><th>进度</th><th>入账份额</th><th className="align-right">操作</th></tr></thead><tbody>{orders.map(o=><tr key={o.id}>
    <td><strong>{fundName(o.fund_id)}</strong><small>{o.external_order_id||o.id.slice(-12)} · {orderTimeLabel(o)}</small></td>
    <td><span className={`pill ${o.side==='BUY'?'sage':'rose'}`}>{o.side==='BUY'?'买入':'卖出'}</span><small>{o.bucket==='TACTICAL'?'做 T':'底仓'}</small></td>
    <td className="numeric">{o.side==='BUY'?`¥ ${cash(o.requested)}`:`${quantity(o.requested)} 份`}</td>
    <td><span className="status-dot"/>{o.automation?.label||statusLabel[o.status]||o.status}<small>{o.automation?.detail||(o.side==='BUY'?`已扣款 ¥${cash(o.paid)}`:`已到账 ¥${cash(o.settled)}`)}</small></td>
    <td className="numeric">{Number(o.confirmed_units)>0?`${quantity(o.confirmed_units)} 份`:'—'}<small>{Number(o.confirmed_units)>0?`费用 ¥${cash(o.confirmed_fee)}${o.automation?.automatic?' · 自动计算':''}`:o.side==='BUY'?`已扣款 ¥${cash(o.paid)}`:'尚未结算'}</small></td>
    <td className="align-right">{!o.close_reason&&!o.cancel_requested&&<button className="text-button" onClick={()=>open('editOrder',o)}>修改</button>}<button className="text-button" onClick={()=>open('orderEvent',o)}>详情 <ArrowRight size={13}/></button></td>
  </tr>)}</tbody></table>{!orders.length&&<div className="empty-inline">{fund?.initialization_pending?'截图交易请在“支付宝原始记录”核对，补充确认后再写入账本。':'还没有交易记录。'}</div>}{compact&&<button className="table-footer" onClick={()=>setPage('records')}>查看全部记录 <ArrowRight size={14}/></button>}</div>;

  return <div className="app">
    <header className="topbar">
      <a className="brand" onClick={()=>setPage('workspace')}><LayoutDashboard size={26}/><span>FundDesk</span></a>
      <nav aria-label="主导航">{pages.map(p=><button key={p.id} className={page===p.id?'nav-active':''} onClick={()=>setPage(p.id)}><p.icon size={19}/>{p.label}</button>)}</nav>
      <select aria-label="账户模式" className="mode-select" value={mode} onChange={e=>{setMode(e.target.value);setSelected('');setPage(restoredPage(localStorage,e.target.value));localStorage.setItem('funding-mode',e.target.value);}}><option value="demo">演示账户</option><option value="real">真实账户</option></select>
    </header>
    <main className="main">
      {mode==='demo'&&<div className="account-notice"><FlaskConical size={18}/>演示账户 · 合成数据</div>}
      {error&&<div className="error-banner">{error}<button onClick={()=>void load().catch(() => {})}>重新加载</button></div>}
      <div className="page-heading"><h1>{pages.find(p=>p.id===page)?.label}</h1><div className="heading-actions">
        {page==='workspace'?<><button className="button buy-button" disabled={!fund} onClick={()=>open('order',{fund_id:fund?.id,side:'BUY',bucket:'BASE'})}><Plus size={18}/>买入</button><button className="button sell-button" disabled={!fund} onClick={()=>open('order',{fund_id:fund?.id,side:'SELL',bucket:'TACTICAL'})}><ArrowUpRight size={18}/>卖出</button></>:<button className="button" onClick={()=>open('order')}>记录交易</button>}
      </div></div>

      {page==='workspace'&&<>
        {portfolio.tracking||mode==='real'?<TrackingSummary portfolio={portfolio} overview={overview} onExplain={()=>open('number')}/>:<section className="summary-grid">
          <div className="summary-card total"><div className="metric-label">总资产</div><div className="metric-value"><span>¥</span>{cash(portfolio.equity)}</div><div className="metric-foot">累计收益 {portfolio.pnl==null?'待对账':`${Number(portfolio.pnl)>0?'+':''}${cash(portfolio.pnl)}`}</div></div>
          <div className="summary-card"><div className="metric-label">持仓市值</div><div className="metric-value"><span>¥</span>{cash(portfolio.market_value)}</div><div className="metric-foot">{portfolio.initialization_pending?'待对账':portfolio.valuation_dates.join(' / ')||'暂无净值'}</div></div>
          <div className="summary-card"><div className="metric-label">可用现金</div><div className="metric-value"><span>¥</span>{cash(portfolio.initialization_pending?null:portfolio.available_cash)}</div></div>
          <div className="summary-card"><div className="metric-label">在途资金</div><div className="metric-value"><span>¥</span>{cash(portfolio.initialization_pending?null:portfolio.in_transit)}</div></div>
        </section>}
        {hasDraft&&<div className="draft-resume"><span>有一笔尚未提交的交易</span><button className="small-primary" onClick={()=>open('order')}>继续填写</button><button className="text-button" onClick={()=>{clearOrderDraft(localStorage,mode);setHasDraft(false);}}>丢弃草稿</button></div>}
        <div className="workbench-layout"><FundSidebar key={mode} funds={funds} catalog={state.fund_sidebar} mode={mode} businessDate={state.business_date} selected={overview?'':selected} overview={overview} onOverview={showOverview} onSelect={selectFund} onAdd={()=>open('fund')} onManage={target=>open('sidebar',target)}/>
        {overview?<OverviewDesk state={state} refreshing={refreshingAll} onRefresh={()=>void refreshAll()} onSelect={selectFund} onExplainFund={id=>open('number',{fund_id:id})} onOrder={(id,side)=>open('order',{fund_id:id,side,bucket:side==='BUY'?'BASE':'TACTICAL'})} onAgendaOrder={target=>open('order',target)} onRecords={()=>{setRecordTab('orders');setPage('records');}} onEdit={o=>open('editOrder',o)}/>:fund?<div className="fund-main">
          <section className="panel fund-overview">
            <div className="fund-heading"><div>
              <h2 title={fund.name}>{fundPresentation?.display_name || fund.name}</h2>
              <div className="fund-kicker">{fund.fund_code||'DEMO'} · {typeLabel[fund.instrument_type]||'基金'}{fundPresentation?.theme&&<span className="pill sage" title="自定义观察标签">{fundPresentation.theme}</span>}{fund.tracking&&<span className="pill khaki">含历史还原</span>}</div>
            </div><div className="button-group">
              <button className="button" onClick={()=>setPage('research')}><FlaskConical size={17}/>查看预测</button>
              {mode==='real'&&fund.automatic_market_supported&&<button className="button" disabled={busy} onClick={()=>void perform(()=>api(`/funds/${fund.id}/market/refresh`,mode,{}),'数据已检查')}><RefreshCw size={17}/>刷新数据</button>}
            </div></div>
            <div className="nav-line"><div><span className="data-caption">{fundDay.ready?`${fundDay.valueLabel} · ${fundDay.date}`:`今日涨跌 · ${fundDay.date}`}{fundDay.asOf&&` · ${dateLabel(fundDay.asOf,'Asia/Shanghai',true).split(' ').at(-1)} 北京时间`}</span>
              <div className="nav-number"><button type="button" className="number-link" aria-label="查看当前价格与收益依据" onClick={()=>open('number',{fund_id:fund.id})}>{fundDay.ready?quantity(fundDay.value,4):fundDay.label}</button>{fundDay.ready&&<span>{pct(fundDay.change)} · {fundDay.label}</span>}</div>
              {!fundDay.official&&fund.nav&&<div className="data-caption">最近正式净值 {fund.nav.effective_date} · {quantity(fund.nav.value,4)}</div>}
            </div>
            </div>
            <FundHoldings fund={fund} onExplain={()=>open('number',{fund_id:fund.id})}/>
            <div className="chart-toolbar"><div className="series-tabs"><button className={series==='nav'?'active':''} onClick={()=>setSeries('nav')}>净值走势</button>{(fund.estimate||fund.estimate_history?.length>0)&&<button className={series==='estimate'?'active':''} onClick={()=>setSeries('estimate')}>参考估值</button>}{series==='estimate'&&<span className="data-caption">北京时间</span>}</div><div className="period-tabs">{[['1m','近1月'],['3m','近3月'],['all','全部']].map(([id,label])=><button key={id} className={period===id?'active':''} onClick={()=>setPeriod(id)}>{label}</button>)}</div></div>
            <Chart key={`${fund.id}-${series}-${period}`} history={chartHistory} trades={chartTrades} openDates={fund.rules?.verified?fund.rules.open_dates:EMPTY} timezone={series==='estimate'?'Asia/Shanghai':tz}/>
            <DailyT fund={fund} reference={plan?.daily_t_items?.find((r:Row)=>r.fund_id===selected)} onSettings={()=>open('tProfile')} onObservation={()=>open('observation',{value_kind:'THIRD_PARTY_ESTIMATE'})} onInvestmentPlan={()=>open('investmentPlan')} onOrder={target=>open('order',target)}/>
          </section>
          <PositionBuckets fund={fund} mode={mode} tz={tz} onSaved={load} onExplain={()=>open('number',{fund_id:fund.id})}/>
          <PlanScenarios key={`scenarios:${mode}:${fund.id}`} fund={fund} mode={mode} revision={state.revision}/>
          <TReviewPanel key={`t-review:${mode}:${fund.id}`} fund={fund} mode={mode} revision={state.revision} onChanged={load}/>
          <ReconciliationPanel key={`reconcile:${mode}:${fund.id}`} fund={fund} mode={mode} onSaved={load} onOpenOrders={id=>{writeRecordFilters(localStorage,mode,{...DEFAULT_RECORD_FILTERS,fundId:id});setSelected(id);setRecordTab('orders');setPage('records');}}/>
          <details className="panel data-details"><summary>持仓与数据来源</summary><div className="section-body">
            <div className="holding-stats"><div><span>持有份额</span><strong>{quantity(fund.initialization_pending?null:fund.units)} 份</strong></div><div><span>可卖出份额</span><strong>{quantity(fund.initialization_pending?null:fund.sellable_units)} 份</strong></div><div><span>剩余成本</span><strong>¥{cash(fund.initialization_pending?null:fund.tracking?fund.tracking_cost:fund.cost)}</strong></div><div><span>冻结份额</span><strong>{quantity(fund.initialization_pending?null:fund.reserved_units)} 份</strong></div></div>
            <div className="button-group"><button className="button" onClick={()=>open('lots',fund)}>持仓批次</button><button className="button" onClick={()=>open('rules',fund)}>产品与费用</button><button className="button" onClick={()=>open('strategy',fund)}>交易规则</button></div>
            {fund.tracking&&<p>起始持仓与费用按交易和净值还原，后续按实际确认回填。历史回款未计作当前现金；跟踪现金仅含开始后登记的入金及到账。</p>}
            {fund.initialization_pending&&<p>交易资料已保存，持仓等待对账。</p>}
            {fund.incomplete&&!fund.tracking&&<p>批次资料不完整，部分收益与费用暂不可计算。</p>}
            {fund.valuation_adjusted&&<p>拆分后估值净值：{quantity(fund.valuation_nav,4)} 元 / 当前份额，原始证据保留。</p>}
            <p>{fund.nav?.source||'暂无净值来源'}{fund.nav_sync&&<> · {fund.nav_sync.count} 个净值日 · <a href={fund.nav_sync.source_url} target="_blank" rel="noreferrer">查看来源</a>{fund.nav_sync.status==='FAILED'&&' · 最近更新失败，保留已有净值'}</>}</p>
            {fund.references?.map((r:Row)=><p key={r.id}>参考记录 {r.effective_date}：{quantity(r.value,4)} · {r.source}</p>)}
            {fund.statement&&<button className="text-button" onClick={()=>{setRecordTab('statements');setPage('records');}}>支付宝原始记录 <ArrowRight size={16}/></button>}
            {['BUY','SELL'].includes(recommendation?.action)&&<div className="legacy-reference"><h3>其他规则结果</h3><div className="button-group"><strong>{actionLabel[recommendation.action]} · ¥{cash(recommendation.amount)}</strong><button className="button" onClick={()=>open('strategy',fund)}>查看规则</button></div><p>{recommendation.reasons.join('；')}</p></div>}
          </div></details>
        </div>:<section className="panel welcome-empty"><h2>添加第一只基金</h2><button className="button" onClick={()=>open('fund')}><Plus size={18}/>添加基金</button></section>}</div>
      </>}

      {page==='records'&&<><section className="panel"><div className="panel-heading"><div className="section-tabs"><button className={recordTab==='orders'?'active':''} onClick={()=>setRecordTab('orders')}>交易进度</button><button className={recordTab==='statements'?'active':''} onClick={()=>setRecordTab('statements')}>支付宝原始记录</button><button className={recordTab==='audit'?'active':''} onClick={()=>setRecordTab('audit')}>资金与审计</button><button className={recordTab==='dividends'?'active':''} onClick={()=>setRecordTab('dividends')}>分红 / 份额变化</button></div><div className="button-group"><button className="button" onClick={()=>open('cash')}><Wallet size={14}/>资金变动</button><button className="button" onClick={()=>open('opening')}><Plus size={14}/>期初持仓</button><a className="button" href={`/api/v1/export?mode=${mode}`} download><Download size={14}/>导出记录</a></div></div>{recordTab==='statements'?<><div className="statement-selector"><label>核对基金 <select aria-label="交易资料基金" value={selected} onChange={e=>setSelected(e.target.value)}>{funds.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label></div><StatementPanel fund={fund}/></>:recordTab==='orders'?(fund?.tracking&&!state.orders.length?<TrackingHistory fund={fund}/>:<OrderFilters mode={mode} orders={state.orders} funds={funds} catalog={state.fund_sidebar} businessDate={state.business_date} resetToken={ordersResetToken} render={(rows,filtered)=>!rows.length&&filtered?<div className="empty-inline">没有符合条件的交易，可重置筛选查看全部。</div>:renderOrders(rows)}/>):recordTab==='audit'?<div className="table-wrap"><table><thead><tr><th>事件</th><th>业务时间</th><th>录入时间</th><th>状态</th><th>操作</th></tr></thead><tbody>{state.audit.map((e:Row)=><tr key={e.id}><td><strong>{e.payload.confirmation_basis==='AUTO_OFFICIAL_NAV'?'按正式净值自动入账':eventLabel[e.kind]||e.kind}</strong><small>{e.payload.fund_id?fundName(e.payload.fund_id):e.payload.note||e.id.slice(-10)}</small></td><td>{dateLabel(e.occurred_at,tz,e.payload.time_precision!=='DAY')}</td><td>{dateLabel(e.recorded_at,tz,true)}</td><td><span className={`pill ${e.reversed?'':'sage'}`}>{e.reversed?'已冲正':'已留存'}</span></td><td>{!e.reversed&&e.kind!=='REVERSAL'&&<button className="text-button" onClick={()=>open('correction',e)}>更正 / 冲正</button>}</td></tr>)}</tbody></table>{!state.audit.length&&<div className="empty-inline">暂无资金或账本事件</div>}</div>:<div className="section-body"><div className="button-group"><button className="primary" onClick={()=>open('corporate')}><Plus size={15}/>登记分红或份额变化</button></div>{state.dividends.map((d:Row)=><div className="list-row" key={d.id}><div><strong>{fundName(d.fund_id)}</strong><small>分红权益 ¥{cash(d.amount)} · 剩余待处理 ¥{cash(d.remaining)}</small></div><button className="button" onClick={()=>open('corporate',d)}>记录到账 / 再投</button></div>)}<p className="muted">分红应收、现金到账、再投资确认分别登记；拆分保持账本总成本。</p></div>}</section></>}

      {page==='research'&&<ResearchWorkspace key={mode} state={state} research={research} forecasting={forecasting} forecastScorecard={forecastScorecard} onSelect={setSelected} mode={mode} selected={selected} predictions={predictions} evaluation={evaluation}
        sidebar={<FundSidebar key={mode} funds={funds} catalog={state.fund_sidebar} mode={mode} businessDate={state.business_date} selected={selected} onSelect={setSelected} onAdd={()=>open('fund')} onManage={target=>open('sidebar',target)}/>}
        onSaved={load} onPolicy={()=>open('researchPolicy')} onImport={()=>open('researchInput')} onPrediction={()=>open('prediction')}/>}

      {page==='data'&&<><div className="data-actions"><button className="panel action-tile" onClick={()=>open('fund')}><Plus/><strong>登记基金</strong><span>代码、份额类别与渠道</span><ArrowUpRight size={16}/></button><button className="panel action-tile" disabled={!funds.length} onClick={()=>open('observation')}><ArrowDownLeft/><strong>录入净值 / 估值</strong><span>明确数据性质与所属日期</span><ArrowUpRight size={16}/></button><button className="panel action-tile" onClick={()=>open('opening')}><Wallet/><strong>建立期初持仓</strong><span>可保留未知成本与日期</span><ArrowUpRight size={16}/></button></div><div className="import-grid"><section className="panel"><div className="panel-heading"><h2><FileInput size={17}/>CSV 导入</h2><span className="pill">先预览 · 后提交</span></div><div className="section-body"><label className="field">资料类型<select value={importKind} onChange={e=>{setImportKind(e.target.value);setPreview(null);}}>{[['observations','净值 / 行情历史'],['funds','基金资料'],['lots','期初持仓批次'],['cash_events','资金变动'],['orders','实际申请'],['confirmations','确认及结算事件']].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><label className="upload-zone"><FolderOpen size={25}/><strong>选择 UTF-8 CSV 文件</strong><span>也可以在下方粘贴内容 · 最多 1000 行 / 2 MB</span><input type="file" accept=".csv,text/csv" onChange={async e=>{const file=e.target.files?.[0];if(file){setCsv(await file.text());setPreview(null);}}}/></label><textarea className="csv-input" aria-label="CSV 内容" placeholder="粘贴包含表头的 CSV 内容…" value={csv} onChange={e=>{setCsv(e.target.value);setPreview(null);}} rows={7}/><div className="button-group"><button className="primary" disabled={!csv||busy} onClick={()=>void perform(async()=>setPreview(await api('/imports/preview',mode,{kind:importKind,content:csv})),'预览已完成，尚未写入账本')}><Search size={15}/>校验并预览</button><a className="text-button" href={`/api/v1/templates/${importKind}`} download><Download size={14}/>下载空白模板</a></div>{preview&&<div className="import-preview"><h3>{preview.errors.length?`${preview.errors.length} 行需要修正`:`${preview.count} 行通过校验`}</h3>{preview.errors.map((e:Row)=><p className="error-text" key={e.line}>第 {e.line} 行：{e.message}</p>)}{!preview.errors.length&&<><p>文件与账本版本已固定。请核对数据内容后提交。</p><div className="preview-table"><table><thead><tr>{Object.keys(preview.rows[0]).slice(0,5).map(k=><th key={k}>{k}</th>)}</tr></thead><tbody>{preview.rows.slice(0,5).map((row:Row,i:number)=><tr key={i}>{Object.values(row).slice(0,5).map((v:any,j:number)=><td key={j}>{String(v)||'—'}</td>)}</tr>)}</tbody></table></div><button className="primary" disabled={busy} onClick={()=>void perform(async()=>{await api('/imports/commit',mode,{id:preview.id,source_hash:preview.source_hash});setPreview(null);setCsv('');},'导入成功，账本已更新')}><Check size={15}/>确认导入 {preview.count} 行</button></>}</div>}</div></section><section className="panel source-panel"><div className="panel-heading"><h2>数据来源状态</h2><ShieldCheck size={17}/></div><div className="section-body"><div className="source-row"><span className="source-icon"><FlaskConical size={17}/></span><div><strong>合成演示数据</strong><small>固定时钟与明确的 Demo 标签</small></div><span className="pill sage">可用</span></div><div className="source-row"><span className="source-icon"><FileInput size={17}/></span><div><strong>手工 / CSV</strong><small>按来源、日期与单位保存</small></div><span className="pill sage">可用</span></div><div className="source-row"><span className="source-icon"><Database size={17}/></span><div><strong>公开披露净值 · 已接入基金</strong><small>日净值自动检查，16:00 后每 10 分钟补查</small></div><span className="pill sage">自动读取</span></div><div className="source-row"><span className="source-icon"><RefreshCw size={17}/></span><div><strong>盘中与收盘估算 · 股票类基金</strong><small>交易时段每 10 分钟采集，收盘补取 15:00 估算；正式净值披露后自动替换。</small></div><span className="pill sage">自动采集</span></div><div className="source-row"><span className="source-icon"><RefreshCw size={17}/></span><div><strong>金价参考 · 008987</strong><small>上金所人民币金价，午盘基准公布后切换；95%业绩基准近似，每10分钟更新。</small></div><span className="pill khaki">本地估算</span></div><div className="inline-note">正式净值、盘中估算和参考标的不会互相覆盖。抓取成功也不会把旧数据标成实时。</div><h3>日常文件交接</h3><p>原始资料放入 <code>input/inbox/</code>，报告和导出从 <code>output/</code> 领取。CSV 中代码须保留前导零，空字段保留未知。</p></div></section></div></>}

      {page==='settings'&&<div className="settings-grid"><MaintenancePanel value={maintenance}/><StoragePanel mode={mode}/><section className="panel"><div className="panel-heading"><h2>界面与本机偏好</h2><Settings2 size={16}/></div><div className="section-body"><label className="field">显示时区<select value={timezone} onChange={e=>{setTimezone(e.target.value);localStorage.setItem('funding-tz',e.target.value);}}><option value="system">跟随系统（{Intl.DateTimeFormat().resolvedOptions().timeZone}）</option><option value="Asia/Tokyo">东京 · Asia/Tokyo</option><option value="Asia/Shanghai">北京 · Asia/Shanghai</option><option value="UTC">UTC</option></select></label><p className="muted">显示时区不改变产品交易日历。交易下单时间固定按北京时间记录；其他资金事件按 Mac 系统时区输入。</p><div className="setting-info"><ShieldCheck size={18}/><div><strong>FundDesk · v0.22.0</strong><small>真实/演示数据独立保存，外部平台订单由你手工操作。</small></div></div><button className="button" onClick={()=>open('cash')}><Wallet size={15}/>记录资金转入 / 转出</button></div></section><section className="panel"><div className="panel-heading"><h2>备份与恢复</h2><button className="primary" disabled={busy} onClick={()=>void perform(()=>api('/backups',mode,{}),'一致性备份已生成')}><Plus size={14}/>创建备份</button></div><div className="section-body"><p className="muted">包含当前账户的账本、规则、行情与预测。恢复会先生成当前库备份，再校验并切换。</p>{backups.map(b=><div className="backup-row" key={b.id}><div><strong>{dateLabel(b.created_at,tz,true)}</strong><small>{b.mode==='demo'?'演示账户':'真实账户'} · {(b.size/1024).toFixed(0)} KB</small></div><a className="icon-button" title="下载备份" href={`/api/v1/backups/${b.id}/download?mode=${mode}`} download><Download size={16}/></a><button className="text-button" onClick={()=>open('restore',b)}>预览恢复</button></div>)}{!backups.length&&<div className="empty-inline">尚无备份。建议在导入和历史更正前创建一份。</div>}</div></section></div>}


    </main>
    {dialog?.type==='number'?<NumberExplanation mode={mode} fundId={dialog.target?.fund_id} onClose={()=>setDialog(null)} onSelectFund={id=>{selectFund(id);setPage('workspace');}}/>:dialog?.type==='researchPolicy'?research&&<ResearchPolicyForm state={state} research={research} mode={mode} onClose={()=>setDialog(null)} onSaved={async()=>{await load();setDialog(null);toast('研究设置已保存');}}/>:dialog?.type==='researchInput'?research&&<ResearchInputForm research={research} mode={mode} onClose={()=>setDialog(null)} onSaved={async()=>{await load();setDialog(null);toast('研究快照已保存');}}/>:dialog?.type==='sidebar'?<SidebarManager catalog={state.fund_sidebar} funds={funds} target={dialog.target} mode={mode} onClose={()=>setDialog(null)} onSaved={async()=>{await load();setDialog(null);toast('基金分类已保存');}}/>:dialog?.type==='investmentPlan'&&fund?<InvestmentPlanForm fund={fund} mode={mode} onClose={()=>setDialog(null)} onSaved={async()=>{await load();setDialog(null);toast('投资计划已保存');}}/>:dialog?.type==='tProfile'&&fund?<TProfileForm fund={fund} mode={mode} onClose={()=>setDialog(null)} onSaved={async()=>{await load();setDialog(null);toast('做 T 强度已保存');}}/>:dialog&&<Forms key={`${mode}:${dialog.type}:${dialog.target?.id||''}`} dialog={dialog} onOpen={(type,target)=>open(type,target)} state={state} mode={mode} onClose={()=>setDialog(null)} onSuccess={async(message:string)=>{if(['order','editOrder'].includes(dialog.type))setOrdersResetToken(old=>old+1);await load();setDialog(null);toast(message);}} toast={toast}/>}
    {notification&&<div className="toast"><Check size={17}/><span>{notification}</span><button onClick={()=>setNotification('')} aria-label="关闭提示"><X size={15}/></button></div>}
  </div>;
}
