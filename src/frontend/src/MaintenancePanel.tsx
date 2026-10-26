import type { Row } from './api';
import { dateLabel } from './api';

export function MaintenancePanel({value}:{value?:Row|null}) {
  if(!value)return null;
  return <section className="panel"><div className="panel-heading"><h2>本机维护</h2><span className="pill sage">v{value.version}</span></div>
    <div className="section-body"><p>记录发生变化后，每日自动保存一份安全快照，最多保留 7 份；手动与升级备份单独保留。</p>
      <div className="list-row"><span>最近自动备份</span><strong>{value.automatic_backup?dateLabel(value.automatic_backup.created_at,'Asia/Shanghai',true):'尚未生成'}</strong></div>
      <div className="list-row"><span>最近更新</span><strong>{value.last_install?`${value.last_install.version} · ${value.last_install.status==='READY'?'已完成':value.last_install.status==='ROLLED_BACK'?'已回退':'待检查'}`:'本机安装'}</strong></div>
      <details><summary>更新与恢复说明</summary><p>{value.data_location}</p><p>{value.restore_notice}</p><p>{String(value.signing).includes('ad-hoc')?'当前为本机临时签名，更新后 macOS 仍可能请求文稿授权。':'应用使用构建时配置的签名身份。'}</p></details>
    </div></section>;
}
