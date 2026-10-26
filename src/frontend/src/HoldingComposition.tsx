import type { Row } from './api';
import { cash, quantity, pct } from './api';
import { signedCash } from './SellImpact';
import { returnBasisLabel } from './returnValuation';
import './holdingComposition.css';

const share = (value: unknown) => value == null ? '—' : Number(value)>0&&Number(value)<.0001 ? '<0.01%' : `${(Number(value)*100).toFixed(2)}%`;
const names: Row = {BASE:'底仓', TACTICAL:'T 仓'};
const keys = ['BASE','TACTICAL'];

export function HoldingComposition({fund,onExplain}:{fund:Row;onExplain?:()=>void}) {
  const view = fund.holding_composition;
  const total = view?.total, groups = view?.groups;
  const ready = view?.status === 'READY';
  // Numbers below only scale SVG/CSS geometry. All money, returns and ratios
  // are supplied by the backend's same-quote, read-only projection.
  const baseShare = Number(groups?.BASE?.weight || 0)*100;
  const tShare = Number(groups?.TACTICAL?.weight || 0)*100;
  return <section className="holding-composition" aria-label="当前基金持仓与盈亏总览" title={returnBasisLabel(fund)}>
    <div className="composition-total">
      <span>总持仓</span><strong className="composition-total-value">¥ {cash(total?.value)}</strong>
      <span>{total?.units == null ? '持仓待填写' : `${quantity(total.units)} 份`}</span>
      <div className="composition-graphic"><svg className="composition-donut" viewBox="0 0 160 160" role="img" aria-label={ready?`仓位占比：底仓 ${share(groups.BASE.weight)}，T 仓 ${share(groups.TACTICAL.weight)}`:view?.status==='EMPTY'?'暂无持仓':'仓位资料待补'}>
        <circle className="composition-track" cx="80" cy="80" r="60"/>
        {ready&&<g transform="rotate(-90 80 80)">
          <circle className="composition-base-stroke" cx="80" cy="80" r="60" pathLength="100" strokeDasharray={`${baseShare} ${100-baseShare}`}><title>{`底仓 ${share(groups.BASE.weight)}`}</title></circle>
          <circle className="composition-t-stroke" cx="80" cy="80" r="60" pathLength="100" strokeDasharray={`${tShare} ${100-tShare}`} strokeDashoffset={-baseShare}><title>{`T 仓 ${share(groups.TACTICAL.weight)}`}</title></circle>
        </g>}
      </svg><span className="composition-chart-label" aria-hidden="true">{ready?'仓位占比':view?.status==='EMPTY'?'暂无持仓':'待补资料'}</span></div>
    </div>
    <div className="composition-detail">
      <div className="composition-profit-totals">
        <div><span>持有盈亏</span><strong>{signedCash(total?.unrealized)} <small>元</small></strong><span>{pct(total?.holding_return)}</span></div>
        <div><span>累计收益</span><strong>{onExplain?<button type="button" className="number-link" onClick={onExplain} aria-label="查看基金累计收益依据">{signedCash(total?.pnl)}</button>:signedCash(total?.pnl)} <small>元</small></strong><span>含已实现收益</span></div>
      </div>
      <div className="composition-table-wrap"><table className="composition-table"><thead><tr><th>仓位</th><th className="numeric">金额 / 份额</th><th className="numeric">仓位占比</th><th className="numeric">持有盈亏</th><th className="numeric">累计盈亏</th></tr></thead><tbody>
        {keys.map(key=>{const g=groups?.[key];return <tr key={key}>
          <td><span className={`composition-key ${key==='BASE'?'base':'tactical'}`}/>{names[key]}</td>
          <td className="numeric"><strong>¥ {cash(g?.value)}</strong><small>{quantity(g?.units)} 份</small></td>
          <td className="numeric">{share(g?.weight)}</td>
          <td className="numeric"><strong>{signedCash(g?.unrealized)}</strong><small>{pct(g?.holding_return)}</small></td>
          <td className="numeric"><strong>{signedCash(g?.pnl)}</strong><small>{g?.pnl_weight==null?'—':`${share(g.pnl_weight)} 构成`}</small></td>
        </tr>;})}
      </tbody></table></div>
      <div className="composition-pnl">
        <span>累计盈亏构成 <small>· 按绝对值</small></span>
        {view?.pnl_status==='READY'?<div className="composition-pnl-bar" role="img" aria-label={keys.map(k=>`${names[k]} ${signedCash(groups[k].pnl)} 元，占 ${share(groups[k].pnl_weight)}`).join('；')}>
          {keys.map(k=><span key={k} className={`${k==='BASE'?'base':'tactical'} ${groups[k].pnl_direction==='LOSS'?'is-loss':''}`} style={{width:`${Number(groups[k].pnl_weight)*100}%`}} title={`${names[k]} ${signedCash(groups[k].pnl)} 元 · ${share(groups[k].pnl_weight)}`}/>)}
        </div>:<div className="composition-pnl-empty">{view?.pnl_status==='FLAT'?'暂无盈亏':'盈亏资料待补'}</div>}
      </div>
      <details className="composition-basis"><summary>口径</summary><p>仓位占比按当前市值计算；持有收益率为持有盈亏 ÷ 剩余成本。累计盈亏包含已实现与持有浮动，构成占比按两仓累计盈亏的绝对值计算，亏损段使用斜纹，正负金额保留。图形不改变持仓或收益。</p><p>{returnBasisLabel(fund)}（日期及估算时间采用北京时间）。</p></details>
    </div>
  </section>;
}
