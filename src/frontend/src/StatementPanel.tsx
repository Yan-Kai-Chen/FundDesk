import { ArrowRight } from 'lucide-react';
import type { Row } from './api';
import { cash, quantity, pct } from './api';

export function StatementPanel({ fund, compact = false, onFull }: { fund?: Row; compact?: boolean; onFull?: () => void }) {
  const statement = fund?.statement;
  if (!statement) return <div className="empty-inline">尚无支付宝交易资料</div>;
  const summary = statement.summary;
  const attribution = (row: Row) => {
    const a = statement.bucket_analysis;
    const setting = a?.assignments?.find((r: Row) => r.row_id === row.id);
    if (!setting) return '待分类';
    if (setting.bucket === 'BASE') return '底仓';
    const buy = a.buys.find((r: Row) => r.id === row.id);
    return buy && setting.t_units && Number(setting.t_units) < Number(buy.units) ? '做 T / 底仓' : '做 T';
  };
  return <section className="panel statement-panel">
    <div className="panel-heading"><h2>支付宝记录 <span className="count">{summary.count}</span></h2><span className="pill khaki">原始资料</span></div>
    <details className="statement-info"><summary>资料与费用</summary>
      <div className="statement-description">
        <p>按支付宝页面时间展示（北京时间）。截图中的日期和当日净值不替代渠道确认结果。</p>
        <p>{statement.history_complete ? '已覆盖全部历史交易' : '历史范围待核对'} · {statement.sources.length} 张截图 · 已去重 {summary.duplicate_count} 行</p>
        <p>买入 {summary.buy_count} 笔，合计 ¥{cash(summary.buy_displayed_total)}；卖出列表金额 ¥{cash(summary.sell_displayed_amount)}，另有 {quantity(summary.pending_sell_units)} 份待核对到账。</p>
        <p>{summary.notice}</p>
      </div>
      {fund?.rules?.annual_expense_total && <div className="statement-fees"><strong>费用规则</strong><span>买入 {pct(fund.rules.purchase_rate).replace('+', '')}；卖出：{fund.rules.redemption_tiers.map((t: Row) => `${t.min_days === 0 ? '不足 ' + t.max_days + ' 天' : t.max_days == null ? '满 ' + t.min_days + ' 天' : t.min_days + ' 至不足 ' + t.max_days + ' 天'} ${pct(t.rate).replace('+', '')}`).join('；')}。</span><span>{fund.instrument_type === 'GOLD_LINKED' && fund.rules.annual_expense_basis ? fund.rules.annual_expense_basis : <>年度管理、托管及销售服务费合计约 {pct(fund.rules.annual_expense_total).replace('+', '')}，已含在净值中，不重复扣款。</>}</span></div>}
    </details>
    {compact ? <button className="table-footer" onClick={onFull}>全部交易 <ArrowRight size={16}/></button> : <div className="table-wrap"><table><thead><tr><th>时间（北京）</th><th>买入 / 卖出</th><th>金额 / 份额</th><th>当日净值</th><th>涨跌</th><th>来源</th></tr></thead><tbody>{[...statement.rows].reverse().map((r: Row) => <tr key={r.id}>
      <td className="numeric">{r.displayed_at.slice(0, 10)}<small>{r.time_precision === 'MINUTE' ? r.displayed_at.slice(11,16) : r.displayed_at.slice(11)}</small></td>
      <td><span className={`pill ${r.side === 'BUY' ? 'sage' : 'rose'}`}>{r.side === 'BUY' ? '买入' : '卖出'}</span><small>{attribution(r)}</small></td>
      <td className="numeric"><strong>{r.unit === 'CNY' ? `¥${cash(r.value)}` : `${quantity(r.value)} 份`}</strong>{r.status === 'PENDING_SETTLEMENT' && <small>预计 {r.expected_settlement_date} 到账</small>}</td>
      <td className="numeric">{quantity(r.same_date_nav?.value, 4)}</td>
      <td className="numeric">{pct(r.same_date_change)}</td>
      <td><details className="row-source"><summary>查看</summary><p>{r.source_refs.map((ref: Row) => `${statement.sources.find((s: Row) => s.sha256 === ref.sha256)?.label || '截图'} · 第 ${ref.row} 行`).join('；')}</p><small>{r.original_category === 'ACTIVITY' ? `原类别：活动，按买入计入。参考净值日 ${r.reference_nav_date || '待补'}：${r.reference_nav_date_basis || '待核对'}` : r.same_date_nav ? '当日净值，非确认单' : '缺失当日净值，未补值'}{r.time_precision === 'MINUTE' && '；截图时间仅到分钟'}</small></details></td>
    </tr>)}</tbody></table></div>}
  </section>;
}
