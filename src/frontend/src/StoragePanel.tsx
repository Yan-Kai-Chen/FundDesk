import { useEffect, useState } from 'react';
import { Database } from 'lucide-react';
import { api } from './api';
import type { Row } from './api';

const size = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(2)} MB`;

export function StoragePanel({mode}:{mode:string}) {
  const [storage,setStorage]=useState<Row|null>(null),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
  useEffect(()=>{let live=true;api('/storage',mode).then(v=>{if(live)setStorage(v);}).catch(e=>{if(live)setMessage(e.message);});return()=>{live=false;};},[mode]);
  return <section className="panel storage-panel"><div className="panel-heading"><h2><Database size={18}/>存储</h2><button className="button" disabled={busy||!storage} onClick={async()=>{
    setBusy(true);setMessage('');try{const r=await api('/storage/cleanup',mode,{});setStorage(r.storage);setMessage('清理完成，交易和关键依据已保留。');}catch(e){setMessage((e as Error).message);}finally{setBusy(false);}
  }}>{busy?'清理中…':'清理缓存'}</button></div><div className="section-body">
    {storage?<><div className="storage-sizes"><span>账户数据 <strong>{size(storage.database_bytes)}</strong></span><span>行情原文缓存 <strong>{size(storage.provider_cache_bytes)}</strong></span></div>
      <p>分钟估值保留近 {storage.policy.intraday_days} 天；原始响应保留 {storage.policy.raw_response_days} 天，上限 {size(storage.policy.raw_response_cap_bytes)}。每次清理后，过期且未关联操作的建议最多保留 {storage.policy.unlinked_plan_limit} 条，最长 {storage.policy.unlinked_plan_days} 天。</p>
      <p>每小时自动清理。交易、正式净值、预测及其引用依据保留；关键原文不受普通缓存期限限制。备份与原始文件单独保留。</p></>:<p>正在检查本地存储…</p>}
    {message&&<p role="status">{message}</p>}
  </div></section>;
}
