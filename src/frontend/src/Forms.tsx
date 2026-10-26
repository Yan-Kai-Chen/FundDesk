import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, X, Info, Calculator, Plus } from 'lucide-react';
import type { Row } from './api';
import { api, key, cash, quantity, localInput, shanghaiInput, submittedTime, dateLabel, eventLabel } from './api';
import { SellPreview } from './SellImpact';
import { readOrderDraft, saveOrderDraft, clearOrderDraft } from './orderDraft';

const titles:Row={fund:'添加自选基金',observation:'录入净值 / 参考数据',cash:'记录资金变动',opening:'建立期初持仓',order:'记录交易',editOrder:'修改交易',orderEvent:'交易详情',strategy:'规则参数',rules:'产品与渠道规则',lots:'持仓批次明细',prediction:'创建 / 登记预测',correction:'更正历史记录',corporate:'分红与份额变化',restore:'预览并恢复备份'};
type Props={dialog:Row,state:Row,mode:string,onClose:()=>void,onOpen:(type:string,target:Row)=>void,onSuccess:(s:string)=>Promise<void>,toast:(s:string)=>void};

export function Forms({dialog,state,mode,onClose,onOpen,onSuccess,toast}:Props){
  const {type,target={}}=dialog;
  const [draft]=useState(()=>type==='order'&&dialog.resume?readOrderDraft(localStorage,mode):null);
  const [entryTime]=useState(()=>type==='order'&&mode==='real'?new Date().toISOString():state.now);
  const restored:Row=draft?.fields||{};
  const draftSaved=useRef(false),draftDirty=useRef(false),draftTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const [draftStatus,setDraftStatus]=useState(draft?'已恢复本机草稿':'');
  const [fundId,setFundId]=useState(restored.fund_id||target.fund_id||dialog.fundId||state.funds[0]?.id||'');
  const fund=state.funds.find((f:Row)=>f.id===fundId);
  const [side,setSide]=useState(restored.side||target.side||'BUY');
  const [bucket,setBucket]=useState(restored.bucket||target.bucket||(type==='order'&&fund?.investment_plan_profile?.enabled?'TACTICAL':'BASE'));
  const bucketExplicit=useRef(!!(restored.bucket||target.bucket)||type!=='order');
  const [sellDraft,setSellDraft]=useState<Row>({requested:String(target.requested||''),occurred_at:new Date(localInput(state.now)).toISOString(),allocations:[]});
  const [readyPreviewKey,setReadyPreviewKey]=useState('');
  const previewReady=useCallback((value:string)=>setReadyPreviewKey(value),[]);
  const sellInput={...sellDraft,bucket};
  const previewKey=JSON.stringify([mode,fundId,side,sellInput,state.revision,fund?.rules?._version,fund?.nav?.id,fund?.daily_t?.today?.quote?.id]);
  const needsSellPreview=type==='order'&&side==='SELL'&&bucket==='TACTICAL';
  const [eventType,setEventType]=useState(target.side==='BUY'?'ORDER_CONFIRMED':'ORDER_CONFIRMED');
  const [manualOrder,setManualOrder]=useState(!target.automation||target.automation.status==='MANUAL');
  const [timePrecision,setTimePrecision]=useState((restored.time_precision||target.time_precision)==='BEFORE_CUTOFF'?'BEFORE_CUTOFF':'EXACT');
  const [corpType,setCorpType]=useState(target.remaining?'DIVIDEND_PAID':'DIVIDEND_DECLARED');
  const [method,setMethod]=useState('LAST_VALUE');
  const [strategyType,setStrategyType]=useState(target.strategy?.type||'TARGET_WEIGHT');
  const [verified,setVerified]=useState(!!target.rules?.verified);
  const [tiers,setTiers]=useState<Row[]>(target.rules?.redemption_tiers||[{min_days:0,max_days:7,rate:''},{min_days:7,max_days:30,rate:''},{min_days:30,max_days:null,rate:''}]);
  const [restore,setRestore]=useState<Row|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const form=useRef<HTMLFormElement>(null);
  const operationKey=useRef(draft?.key||key());
  const strategy=target.strategy||fund?.strategy||{},params=strategy.params||{},rule=target.rules||{};
  const businessDate=state.business_date||state.now.slice(0,10);
  const options = type==='orderEvent'?(target.closed?(target.side==='BUY'?(Number(target.refund_due)>0?['ORDER_REFUNDED']:[]):(Number(target.unsettled)>0?['ORDER_SETTLED']:[])):
    target.side==='BUY'?['ORDER_CONFIRMED','ORDER_PAID','ORDER_CANCEL_REQUESTED','ORDER_CANCELED','ORDER_REJECTED']:['ORDER_CONFIRMED','ORDER_CANCEL_REQUESTED','ORDER_CANCELED','ORDER_REJECTED']):[];
  useEffect(()=>{if(type==='orderEvent'&&options.length)setEventType(options[0]);},[]);
  useEffect(()=>{if(type==='restore')api('/restore/preview',mode,{id:target.id}).then(setRestore).catch(e=>setError(e.message));},[]);
  useEffect(()=>{const handler=(e:KeyboardEvent)=>{if(e.key==='Escape'&&!busy){persistRef.current();onClose();}};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);},[busy,onClose]);
  const input=(name:string,label:string,value:unknown='',kind='text',required=true,help='')=><label className="field" key={name}>{label}<input name={name} type={kind} defaultValue={value==null?'':String(value)} required={required} step={kind==='number'?'any':undefined}/>{help&&<small>{help}</small>}</label>;
  const fundSelect=<label className="field">基金<select name="fund_id" value={fundId} onChange={e=>{setFundId(e.target.value);if(!bucketExplicit.current)setBucket(state.funds.find((f:Row)=>f.id===e.target.value)?.investment_plan_profile?.enabled?'TACTICAL':'BASE');setReadyPreviewKey('');setSellDraft(old=>({...old,allocations:[]}));}} required><option value="" disabled>请选择基金</option>{state.funds.map((f:Row)=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label>;
  const timeInput=input('time','实际发生时间（Mac 系统时区）',localInput(state.now),'datetime-local');
  const tradeTimeInput=(value:string)=><><label className="field">下单时间记录方式<select name="time_precision" value={timePrecision} onChange={e=>setTimePrecision(e.target.value)}><option value="EXACT">填写具体时间</option><option value="BEFORE_CUTOFF">已确定 15:00 前，具体时间不详</option></select></label>{timePrecision==='BEFORE_CUTOFF'?input('submitted_date','实际下单日期（北京时间）',shanghaiInput(value).slice(0,10),'date'):input('submitted_time','实际下单时间（北京时间）',shanghaiInput(value),'datetime-local')}</>;
  const at=(data:Row)=>new Date(data.time||localInput(state.now)).toISOString();
  const tradeAt=(data:Row)=>submittedTime(timePrecision==='BEFORE_CUTOFF'?data.submitted_date+'T00:00':data.submitted_time);
  const syncSellDraft=()=>{
    if(type!=='order'||!form.current)return;
    const data=Object.fromEntries(new FormData(form.current).entries());
    let time='';
    try{time=tradeAt(data);}catch{/* An incomplete date remains an incomplete preview. */}
    const selectedFund=state.funds.find((f:Row)=>f.id===data.fund_id);
    setSellDraft({requested:String(data.requested||''),occurred_at:time,
      allocations:(selectedFund?.lots||[]).map((lot:Row)=>({lot_id:lot.id,units:data['lot_'+lot.id]})).filter((r:Row)=>r.units&&String(r.units).trim()!=='')});
  };
  const persistDraft=()=>{
    if(type!=='order'||!form.current||!draftDirty.current||draftSaved.current)return;
    const values:Row=Object.fromEntries(new FormData(form.current).entries());
    values.paid_immediately=!!values.paid_immediately;values.auto_confirm=!!values.auto_confirm;
    const ok=saveOrderDraft(localStorage,mode,values,operationKey.current);
    setDraftStatus(ok?'草稿已保存在本机':'本机草稿暂时无法保存，请保持窗口打开');
  };
  const persistRef=useRef(persistDraft);persistRef.current=persistDraft;
  const queueDraft=()=>{
    if(type!=='order')return;draftDirty.current=true;
    if(draftTimer.current)clearTimeout(draftTimer.current);
    draftTimer.current=setTimeout(()=>persistRef.current(),300);
  };
  const close=()=>{persistDraft();onClose();};
  const discard=()=>{draftSaved.current=true;clearOrderDraft(localStorage,mode);onClose();};
  useEffect(()=>{
    if(!draft||!form.current)return;
    for(const [name,value] of Object.entries(draft.fields)){
      const element=form.current.elements.namedItem(name) as HTMLInputElement|null;
      if(element&& !['fund_id','side','bucket','time_precision'].includes(name)){
        if(element.type==='checkbox')element.checked=value===true;else element.value=String(value);
      }
    }
    syncSellDraft();
  },[]);
  useEffect(()=>{syncSellDraft();if(draftDirty.current)queueDraft();},[timePrecision,side,fundId,bucket]);
  useEffect(()=>{
    const flush=()=>persistRef.current();
    window.addEventListener('pagehide',flush);
    return()=>{window.removeEventListener('pagehide',flush);if(draftTimer.current)clearTimeout(draftTimer.current);flush();};
  },[]);

  const fillEstimate=async()=>{
    if(!form.current)return;
    const values=Object.fromEntries(new FormData(form.current).entries());
    try{
      const result=await api('/orders/preview',mode,{fund_id:target.fund_id,side:target.side,requested:target.side==='BUY'?(values.gross||target.unconfirmed_paid||target.requested):(values.units||target.remaining_requested),nav:values.nav,trade_date:values.nav_date});
      for(const field of ['units','gross','fee','net']){const element=form.current.elements.namedItem(field) as HTMLInputElement|null;if(element)element.value=String(result[field]);}
      toast('已填入估计值，请按渠道实际确认结果逐项核对后提交');
    }catch(e){setError((e as Error).message);}
  };

  const submit=async(e:React.FormEvent)=>{
    e.preventDefault();if(!form.current)return;
    if(needsSellPreview&&readyPreviewKey!==previewKey){setError('请先核对当前卖出份额的预期收益');return;}
    setError('');setBusy(true);
    const d:Row=Object.fromEntries(new FormData(form.current).entries()),idempotency=operationKey.current;
    const event=async(kind:string,payload:Row)=>api('/events',mode,{kind,payload,key:idempotency,occurred_at:at(d),expected_revision:state.revision});
    try{
      if(type==='fund')await api('/funds',mode,{name:d.name,fund_code:d.fund_code,share_class:d.share_class,channel:d.channel,instrument_type:d.instrument_type});
      if(type==='observation')await api('/observations',mode,{fund_id:fundId,value_kind:d.value_kind,value:d.value,effective_date:d.effective_date,source:d.source,published_at:d.published?new Date(d.published).toISOString():null,as_of:d.value_kind==='OFFICIAL_NAV'?null:at(d)});
      if(type==='cash')await event(d.direction,{amount:d.amount,note:d.note});
      if(type==='opening')await event('OPENING_LOT',{fund_id:fundId,units:d.units,cost:d.cost||null,acquired:d.acquired||null,available_from:d.available_from||null,snapshot_date:businessDate,bucket:d.bucket});
      if(type==='order'){
        const allocations=(fund?.lots||[]).map((lot:Row)=>({lot_id:lot.id,units:d['lot_'+lot.id]})).filter((a:Row)=>a.units&&String(a.units).trim()!=='');
        await api('/orders',mode,{fund_id:fundId,side,bucket,cash_source:mode==='real'?'EXTERNAL':'TRACKED',auto_confirm:mode==='real'&&!!d.auto_confirm,requested:d.requested,external_order_id:d.external_order_id||'',key:idempotency,occurred_at:tradeAt(d),time_precision:timePrecision,expected_revision:state.revision,allocations:side==='SELL'?allocations:[],paid_immediately:!!d.paid_immediately,
          ...(target.source_recommendation?{source_recommendation:target.source_recommendation,strategy_id:target.strategy_id}:{})});
      }
      if(type==='editOrder')await api(`/orders/${target.id}/edit`,mode,{fund_id:fundId,side,bucket,requested:d.requested,
        paid_amount:side==='BUY'?d.paid_amount:'0',external_order_id:d.external_order_id||'',auto_confirm:!!d.auto_confirm,
        submitted_at:tradeAt(d),time_precision:timePrecision,key:idempotency,expected_revision:state.revision,expected_order_updated_at:target.updated_at});
      if(type==='orderEvent'){
        let payload:Row={order_id:target.id};
        if(eventType==='ORDER_CONFIRMED')payload={...payload,units:d.units,nav:d.nav,gross:d.gross,fee:d.fee,net:d.net,nav_date:d.nav_date,confirm_date:d.confirm_date,available_from:d.available_from||null,is_final:!!d.is_final,confirmation_external_id:d.confirmation_external_id||undefined};
        else if(['ORDER_PAID','ORDER_SETTLED','ORDER_REFUNDED'].includes(eventType))payload.amount=d.amount;
        await event(eventType,payload);
      }
      if(type==='strategy'){
        const next:Row={...params};
        for(const field of ['single_amount','daily_limit','cash_reserve','min_value','max_value','min_order','group_limit','anchor_nav'])if(d[field]!==undefined)next[field]=d[field]||'0';
        for(const field of ['target_percent','band_percent','buy_percent','sell_percent','max_fee_percent'])next[field]=d[field]||'0';
        next.cooldown_days=Number(d.cooldown_days||0);
        await api(`/funds/${fundId}/strategy`,mode,{enabled:!!d.enabled,type:d.strategy_type,params:next,expected_version:strategy._version||0},'PUT');
      }
      if(type==='rules'){
        await api(`/funds/${fundId}/rules`,mode,{...rule,verified,source:d.source,effective_from:d.effective_from,expected_version:rule._version||0,lot_method:'FIFO',purchase_method:'EXTERNAL_RATIO',purchase_rate_percent:d.purchase_rate_percent||'0',unit_step:d.unit_step||'0.01',
          redemption_tiers:tiers.map((_,i)=>({min_days:Number(d[`tier_min_${i}`]),max_days:d[`tier_max_${i}`]?Number(d[`tier_max_${i}`]):null,rate_percent:d[`tier_rate_${i}`]||'0'})),
          open_dates:(d.open_dates||'').split(/[\s,，]+/).filter(Boolean).sort(),cutoff:d.cutoff||'15:00:00',timezone:'Asia/Shanghai',subscriptions_open:!!d.subscriptions_open,redemptions_open:!!d.redemptions_open},'PUT');
      }
      if(type==='prediction')await api('/predictions',mode,{fund_id:fundId,method,target_date:d.target_date,key:idempotency,...(method==='EXTERNAL'?{predicted_nav:d.predicted_nav,source:d.source,generated_at:at(d)}:{})});
      if(type==='correction'){
        let replacement=null;
        if(!d.reverse_only){const payload={...target.payload};for(const field of ['amount','units','cost','acquired','available_from','nav','gross','fee','net','nav_date','confirm_date'])if(d['edit_'+field]!==undefined)payload[field]=d['edit_'+field]||null;replacement={kind:target.kind,payload,occurred_at:target.occurred_at};}
        await api('/corrections',mode,{event_id:target.id,reason:d.reason,occurred_at:at(d),key:idempotency,expected_revision:state.revision,replacement});
      }
      if(type==='corporate'){
        const payload:Row={fund_id:fundId};
        if(corpType==='SPLIT')payload.factor=d.factor;
        else{payload.amount=d.amount;if(['DIVIDEND_PAID','DIVIDEND_REINVESTED'].includes(corpType))payload.dividend_id=d.dividend_id;}
        if(corpType==='DIVIDEND_REINVESTED')Object.assign(payload,{units:d.units,nav:d.nav,acquired:d.acquired||null,available_from:d.available_from||null});
        await event(corpType,payload);
      }
      if(type==='restore')await api('/restore/commit',mode,{id:target.id,sha256:restore!.manifest.sha256,confirm:!!d.confirm_restore});
      if(type==='order'){draftSaved.current=true;clearOrderDraft(localStorage,mode);}
      await onSuccess(type==='restore'?'恢复已完成，恢复前备份已保留':type==='editOrder'?'交易已修改，已重新核对净值日与入账结果':'已保存，本地记录已更新');
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };

  const readOnly=type==='lots'||(type==='orderEvent'&&(!options.length||!manualOrder));
  return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)close();}}><section className={`modal ${['rules','lots','strategy','correction'].includes(type)?'wide':''}`} role="dialog" aria-modal="true" aria-label={titles[type]}><div className="modal-header"><div><span className="eyebrow">{mode==='demo'?'演示账户':'本地记录'}</span><h2>{titles[type]}</h2></div><button className="icon-button" aria-label="关闭对话框" onClick={close} disabled={busy}><X size={20}/></button></div><form ref={form} onSubmit={submit} onInput={()=>{syncSellDraft();queueDraft();}} onChange={queueDraft}><div className="modal-body">
    {error&&<div className="error-banner" role="alert">{error}</div>}
    {type==='fund'&&<><div className="inline-note"><Info size={15}/>按实际基金代码与份额类别登记；加入自选不会创建持仓。</div>{input('name','基金名称') }<div className="form-grid">{input('fund_code','六位基金代码','','text',mode==='real','例如 000001，保留前导零')}<label className="field">份额类别<select name="share_class"><option>A</option><option>C</option><option>E</option><option>其他</option></select></label></div>{input('channel','实际渠道','手工记录')}<label className="field">产品类别<select name="instrument_type"><option value="OPEN_END_FUND">场外开放式基金</option><option value="EQUITY_FUND">权益基金</option><option value="BOND_FUND">债券基金</option><option value="GOLD_LINKED">黄金 ETF 联接基金</option></select></label></>}
    {type==='observation'&&<>{fundSelect}<div className="form-grid"><label className="field">数据性质<select name="value_kind" defaultValue={target.value_kind||'OFFICIAL_NAV'}><option value="OFFICIAL_NAV">正式单位净值（按来源核对）</option><option value="THIRD_PARTY_ESTIMATE">第三方盘中估值</option><option value="LOCAL_ESTIMATE">本地参考估值</option><option value="MANUAL_VALUE">其他人工参考值</option></select></label>{input('effective_date','数值所属日期',businessDate,'date')}</div>{input('value','数值（人民币 / 份）','','number')}{input('source','来源或核对依据','','text',true,'填写基金公司、渠道页面或文件名称')}{input('published','实际披露时间（未知可留空）','','datetime-local',false)}{timeInput}<div className="inline-note">手工录入保留人工来源标记。录入过去日期不表示本机当时已经获取该数据。</div></>}
    {type==='cash'&&<><label className="field">资金方向<select name="direction"><option value="CASH_DEPOSIT">外部资金转入工作台跟踪范围</option><option value="CASH_WITHDRAWAL">从工作台跟踪范围转出</option></select></label>{input('amount','实际金额（元）','','number')}{timeInput}{input('note','说明','','text',false)}<div className="inline-note">内部申购、赎回到账和分红应从对应订单或分红入口登记，不重复作为入金。</div></>}
    {type==='opening'&&<>{fundSelect}<label className="field">持仓归属<select name="bucket"><option value="BASE">底仓</option><option value="TACTICAL">做 T</option></select></label>{input('units','已经确认的期初份额','','number')}<div className="form-grid">{input('cost','对应历史总成本（元，未知可留空）','','number',false)}{input('acquired','持有期起算日期（未知可留空）','','date',false)}</div>{input('available_from','已经核实的可赎回日期（未知可留空）','','date',false,'日期未知的份额暂不纳入精确卖出建议')}{timeInput}<div className="inline-note">不要把相同时间范围内的期初持仓与历史申购重复导入。未知成本与日期会如实保留。</div></>}
    {type==='order'&&<>{draftStatus&&<p className="draft-status" role="status">{draftStatus} · 不会自动提交</p>}{fundSelect}
      <div className="form-grid"><label className="field">买入 / 卖出<select name="side" value={side} onChange={e=>{setSide(e.target.value);setReadyPreviewKey('');}}><option value="BUY">买入（按金额）</option><option value="SELL">卖出（按份额）</option></select></label><label className="field">仓位<select aria-label="交易归属" name="bucket" value={bucket} onChange={e=>{bucketExplicit.current=true;setBucket(e.target.value);setReadyPreviewKey('');}}><option value="BASE">底仓</option><option value="TACTICAL">做 T</option></select></label></div>
      {input('requested',side==='BUY'?'买入金额（元）':'卖出份额',target.requested||'','number')}
      {needsSellPreview&&<SellPreview key={`${mode}:${fundId}`} fundId={fundId} mode={mode} input={sellInput} requestKey={previewKey} onReady={previewReady} onEditBuy={id=>{const order=state.orders.find((o:Row)=>o.id===id);if(order){persistDraft();onOpen('editOrder',order);}}}/>}
      <p className="field-help">{side==='BUY'?(bucket==='TACTICAL'?'确认后计入 T 仓。':'确认后计入底仓。'):(bucket==='TACTICAL'?'先扣 T 仓，不足补扣底仓，整笔盈亏归 T。':'只扣底仓，盈亏归底仓。')}{fund?.initialization_pending?'持仓待对账。':''}</p>
      {side==='BUY'&&<label className="check-field"><input type="checkbox" name="paid_immediately"/>平台已按买入金额实际扣款</label>}
      {mode==='real'&&<p className="field-help">正式净值就绪并到确认日后，自动计算份额、费用并计入所选仓位。</p>}
      {tradeTimeInput(entryTime)}
      <details className="form-details"><summary>订单号与入账设置</summary>{input('external_order_id','渠道订单号','','text',false)}{mode==='real'&&<label className="check-field"><input type="checkbox" name="auto_confirm" defaultChecked/>按正式净值自动入账</label>}</details>
      {side==='SELL'&&<details className="form-details"><summary>批次分配</summary><p className="field-help">{fund?.rules?.verified?'可留空，按渠道 FIFO 分配。':'批次规则待核实，请按实际申请填写。'}</p>{fund?.lots.map((l:Row)=><div className="allocation-row" key={l.id}><div><strong>{l.acquired||'日期未知'}</strong><small>{quantity(l.units)} 份 · 冻结 {quantity(l.reserved)}</small></div><input name={'lot_'+l.id} aria-label={`批次 ${l.acquired||l.id} 本次占用份额`} type="number" step="any" placeholder="本次占用份额"/></div>)}</details>}
      <p className="field-help">补录请填写实际下单时间，录入时间单独保留。{mode==='real'?'无需填写账户现金余额。':'买卖需在实际平台完成。'}</p>
    </>}
    {type==='editOrder'&&<>
      {fundSelect}
      <div className="form-grid"><label className="field">买入 / 卖出<select value={side} onChange={e=>setSide(e.target.value)}><option value="BUY">买入（按金额）</option><option value="SELL">卖出（按份额）</option></select></label><label className="field">仓位<select value={bucket} onChange={e=>setBucket(e.target.value)}><option value="BASE">底仓</option><option value="TACTICAL">做 T</option></select></label></div>
      {input('requested',side==='BUY'?'买入金额（元）':'卖出份额',target.requested,'number')}
      {side==='BUY'&&input('paid_amount','平台已扣款金额（元）',target.paid,'number')}
      {tradeTimeInput(target.submitted_at)}
      {input('external_order_id','渠道订单号',target.external_order_id||'','text',false)}
      <label className="check-field"><input name="auto_confirm" type="checkbox" defaultChecked={target.auto_confirm!==false}/>按正式净值自动入账</label>
      <p className="field-help">按实际下单时间判断 15:00 截止。修改原交易，不会重复新增；自动计算结果会重算，平台实际确认保留。</p>
      <small>最初录入：{dateLabel(target.recorded_at,undefined,true)}</small>
    </>}
    {type==='orderEvent'&&<>
      <div className="order-summary"><strong>{state.funds.find((f:Row)=>f.id===target.fund_id)?.name}</strong><span>{target.side==='BUY'?'买入':'卖出'}申请 {target.side==='BUY'?`¥${cash(target.requested)}`:`${quantity(target.requested)} 份`}</span><small>{target.automation?.automatic?'自动计算':'已确认'} {quantity(target.confirmed_units)} 份 · 费用 ¥{cash(target.confirmed_fee)}</small></div>
      {target.automation?.label&&<div className="inline-note"><strong>{target.automation.label}</strong><p>{target.automation.detail}</p>{target.automation.nav_date&&<p>采用 {target.automation.nav_date} 正式净值 {target.automation.nav} · 入账日 {target.automation.confirm_date}</p>}{target.automation.automatic&&<p>已计入本地持仓与收益；这是按正式净值计算的记录，未读取支付宝确认回执。</p>}</div>}
      {target.automation?.correction_event&&<button type="button" className="button" onClick={()=>onOpen('correction',target.automation.correction_event)}>校正平台实际结果</button>}
      {!target.close_reason&&!target.cancel_requested&&<button type="button" className="button" onClick={()=>onOpen('editOrder',target)}>修改交易信息</button>}
      {!manualOrder&&options.length>0&&<button type="button" className="text-button" onClick={()=>setManualOrder(true)}>{target.closed&&target.side==='SELL'?'登记实际到账（可选）':'手工记录扣款、撤单或确认结果'}</button>}
      {manualOrder&&options.length>0?<>
        <label className="field">记录事项<select value={eventType} onChange={e=>setEventType(e.target.value)}>{options.map(t=><option value={t} key={t}>{eventLabel[t]}</option>)}</select></label>
        {eventType==='ORDER_CONFIRMED'?<>
          <div className="form-grid">{input('nav_date','实际净值日期',businessDate,'date')}{input('confirm_date','实际确认日期',businessDate,'date')}{input('nav','实际确认单位净值',state.funds.find((f:Row)=>f.id===target.fund_id)?.nav?.value||'','number')}{input('units','本次实际确认份额','','number')}{input('gross',target.side==='BUY'?'本次确认总支出（元）':'本次赎回总额（元）',target.side==='BUY'?target.unconfirmed_paid||target.requested:'','number')}{input('fee','本次实际费用（元）','','number')}{input('net',target.side==='BUY'?'扣费后净申购额（元）':'本次净到账应收（元）','','number')}{input('available_from','实际可赎回日期（未知可留空）','','date',false)}</div>
          <button type="button" className="button" onClick={()=>void fillEstimate()}><Calculator size={14}/>按规则计算，再核对</button>{input('confirmation_external_id','渠道确认编号（有则填写）','','text',false)}<label className="check-field"><input type="checkbox" name="is_final" defaultChecked/>此订单所有部分已结束</label><label className="check-field"><input type="checkbox" required/>已对照平台实际确认结果核对以上字段</label>
        </>:['ORDER_PAID','ORDER_SETTLED','ORDER_REFUNDED'].includes(eventType)?input('amount','本次实际金额（元）',eventType==='ORDER_PAID'?target.unpaid_remaining:eventType==='ORDER_SETTLED'?target.unsettled:target.refund_due,'number'):<div className="inline-note">{eventType==='ORDER_CANCEL_REQUESTED'?'仅记录撤单申请，收到渠道确认前继续保留冻结。':'按渠道实际结果登记；已扣款的剩余款项将在实际退款后记录。'}</div>}{timeInput}
      </>:manualOrder?<div className="inline-note">本订单已结束。历史记录可从“资金与审计”更正。</div>:null}
      {target.automation?.assumption&&<details className="form-details"><summary>自动入账依据</summary><p>{target.automation.assumption}</p><p>按北京时间 15:00 截止及参考交易日历安排；只采用对应日期的正式净值，不采用盘中估算。实际渠道结果可随时校正。</p></details>}
    </>}
    {type==='lots'&&<><h3>{target.name}</h3><div className="table-wrap"><table><thead><tr><th>持有期起点</th><th>剩余份额</th><th>冻结</th><th>剩余成本</th><th>可赎回日期</th></tr></thead><tbody>{target.lots?.map((l:Row)=><tr key={l.id}><td>{l.acquired||'未知'}</td><td>{quantity(l.units)}</td><td>{quantity(l.reserved)}</td><td>{cash(l.cost)}</td><td>{l.available_from||'未知'}</td></tr>)}</tbody></table></div>{!target.lots?.length&&<p>尚未登记持仓批次。</p>}<div className="inline-note">历史起始批次含净值还原值；本次实际确认以渠道结果为准。持有期和费用保留来源。</div></>}
    {type==='strategy'&&<><h3>{target.name}</h3><label className="check-field"><input type="checkbox" name="enabled" defaultChecked={strategy.enabled}/>启用这只基金的个人规则（否则保持观察）</label><label className="field">规则类型<select name="strategy_type" value={strategyType} onChange={e=>setStrategyType(e.target.value)}><option value="MANUAL_BUCKETS">底仓与做 T · 手动记录</option><option value="TARGET_WEIGHT">目标仓位区间</option><option value="ANCHORED_THRESHOLD">固定锚点阈值</option></select></label>{strategyType==='MANUAL_BUCKETS'?<div className="inline-note">底仓与T已按起始时份额划分。此模式直接记录买卖归属；不自动设定涨跌阈值，也不生成金额买卖建议。</div>:<><div className="form-grid">{input('target_percent','目标仓位（%）',strategy.display_percent?.target_percent??'','number')}{input('band_percent','允许偏离（百分点）',strategy.display_percent?.band_percent??'','number')}{input('anchor_nav','阈值规则初始锚点净值',params.anchor_nav||'','number',false)}{input('buy_percent','下跌触发幅度（%）',strategy.display_percent?.buy_percent??'','number')}{input('sell_percent','上涨触发幅度（%）',strategy.display_percent?.sell_percent??'','number')}{input('max_fee_percent','允许赎回费率上限（%）',strategy.display_percent?.max_fee_percent??'','number')}</div><h3>资金与持仓约束</h3><div className="form-grid">{[['single_amount','单笔金额上限'],['daily_limit','单日申请规模上限'],['cash_reserve','组合现金保留额'],['min_value','保留底仓金额'],['max_value','最高持仓金额'],['min_order','最小计划金额'],['group_limit','黄金共同暴露上限']].map(([id,label])=>input(id,label+'（元）',params[id]||'','number'))}{input('cooldown_days','确认后冷却天数',params.cooldown_days||0,'number')}</div><div className="inline-note">百分比填写 2 表示 2%。真实账户参数需由你确定；保存新版本不会改写旧建议证据。</div></>}</>}
    {type==='rules'&&<><h3>{target.name}</h3><label className="check-field"><input type="checkbox" checked={verified} onChange={e=>setVerified(e.target.checked)}/>我已按产品及渠道资料核实以下规则</label>{input('source','核实依据 / 文件或网页来源',rule.source||'','text',verified)}<div className="form-grid">{input('effective_from','本规则生效日期',rule.effective_from||'','date',verified)}{input('cutoff','申请截止时间（北京时间）',rule.cutoff||'15:00:00','time')}{input('purchase_rate_percent','外扣比例申购费（%）',rule.purchase_rate_percent??'','number',verified)}{input('unit_step','最小份额步长',rule.unit_step||'0.01','number')}</div><h3>赎回费率档位（含下限，不含上限）</h3>{tiers.map((t,i)=><div className="tier-row" key={i}>{input(`tier_min_${i}`,'起始天数',t.min_days,'number',verified)}{input(`tier_max_${i}`,'结束天数（末档留空）',t.max_days,'number',false)}{input(`tier_rate_${i}`,'费率（%）',t.rate_percent??'','number',verified)}</div>)}<button type="button" className="text-button" onClick={()=>setTiers([...tiers,{min_days:0,max_days:null,rate:''}])}><Plus size={13}/>添加档位</button><label className="field">已核实的开放日期清单<textarea name="open_dates" rows={4} defaultValue={(rule.open_dates||[]).join(', ')} placeholder="YYYY-MM-DD，用逗号或换行分隔"/></label><div className="form-grid"><label className="check-field"><input type="checkbox" name="subscriptions_open" defaultChecked={rule.subscriptions_open!==false}/>允许申购</label><label className="check-field"><input type="checkbox" name="redemptions_open" defaultChecked={rule.redemptions_open!==false}/>允许赎回</label></div><div className="inline-note">首版费用计算器支持已核实的外扣比例申购费、FIFO 及逐批次赎回费。其他规则仍可记录实际确认结果，费用预览保持未核实。</div></>}
    {type==='prediction'&&<>{fundSelect}<label className="field">方法 / 来源<select value={method} onChange={e=>setMethod(e.target.value)}><option value="LAST_VALUE">上一有效净值基线</option><option value="MEAN_RETURN_5">五期平均收益基线</option><option value="EXTERNAL">登记外部预测（保留来源）</option></select></label>{input('target_date','目标净值日期',fund?.rules?.open_dates?.find((d:string)=>d>businessDate)||'','date')}{method==='EXTERNAL'&&<>{input('predicted_nav','外部预测净值','','number')}{input('source','预测来源 / 文件名称')}{timeInput}</>}<div className="inline-note">至少需要 20 个历史正式净值点。历史变动情景带不是置信区间；结果仅作研究与观察。</div></>}
    {type==='correction'&&<><div className="inline-note">原事件：{eventLabel[target.kind]||target.kind}。更正保留原件及原因；如果影响后续订单，校验会阻止不一致的修改。</div>{Object.entries(target.payload).filter(([field])=>['amount','units','cost','acquired','available_from','nav','gross','fee','net','nav_date','confirm_date'].includes(field)).map(([field,value])=>input('edit_'+field,({amount:'金额',units:'份额',cost:'成本',acquired:'持有期起算日',available_from:'可赎回日期',nav:'确认净值',gross:'确认总金额',fee:'费用',net:'确认净额',nav_date:'净值日期',confirm_date:'确认日期'} as Row)[field],value,['acquired','available_from','nav_date','confirm_date'].includes(field)?'date':'number',false))}<label className="check-field"><input type="checkbox" name="reverse_only"/>仅冲正原事件，不创建替代事件</label>{input('reason','更正原因')}{timeInput}</>}
    {type==='corporate'&&<>{fundSelect}<label className="field">业务类型<select value={corpType} onChange={e=>setCorpType(e.target.value)}><option value="DIVIDEND_DECLARED">确认现金分红权益（待到账）</option><option value="DIVIDEND_PAID">已发生的现金分红到账</option><option value="DIVIDEND_REINVESTED">红利再投资确认</option><option value="SPLIT">份额拆分 / 合并</option></select></label>{['DIVIDEND_PAID','DIVIDEND_REINVESTED'].includes(corpType)&&<label className="field">关联分红权益<select name="dividend_id" defaultValue={target.id||''} required><option value="" disabled>选择已登记权益</option>{state.dividends.filter((d:Row)=>d.fund_id===fundId&&Number(d.remaining)>0).map((d:Row)=><option key={d.id} value={d.id}>剩余 ¥{cash(d.remaining)} · {d.id.slice(-8)}</option>)}</select></label>}{corpType==='SPLIT'?input('factor','份额变化倍数（例如 2 表示一份变两份）','','number'):input('amount','本次实际金额（元）',target.remaining||'','number')}{corpType==='DIVIDEND_REINVESTED'&&<div className="form-grid">{input('units','实际新增份额','','number')}{input('nav','实际再投资净值','','number')}{input('acquired','持有期起算日',businessDate,'date')}{input('available_from','实际可赎回日','','date',false)}</div>}{timeInput}</>}
    {type==='restore'&&restore&&<><div className="inline-note">模式与校验和、数据库完整性和账本重放均已通过预览。恢复前将自动备份当前数据。</div><div className="restore-counts">{Object.entries(restore.counts).map(([name,count])=><div key={name}><span>{({events:'账本事件',observations:'行情版本',predictions:'预测记录',plans:'建议快照'} as Row)[name]}</span><strong>{String(count)}</strong></div>)}</div><p>备份时现金 ¥{cash(restore.cash)}，账本版本 {restore.revision}。</p><label className="check-field"><input type="checkbox" name="confirm_restore" required/>确认用此备份恢复当前{mode==='demo'?'演示':'真实'}账户</label></>}
    </div><div className="modal-footer"><button className="button" type="button" onClick={close} disabled={busy}>{readOnly?'关闭':type==='order'?'稍后继续':'取消'}</button>{type==='order'&&<button className="text-button" type="button" onClick={discard} disabled={busy}>放弃填写</button>}{!readOnly&&<button className="primary" type="submit" disabled={busy||(type==='restore'&&!restore)||(needsSellPreview&&readyPreviewKey!==previewKey)}>{busy?'正在保存…':type==='restore'?'确认恢复':'核对后保存'}{!busy&&(type==='restore'?<Check size={15}/>:<ArrowRight size={15}/>)}</button>}</div></form></section></div>;
}
