"""Explain existing marked returns; never produce another P&L calculation."""
from app.domain.finance import ZERO, dec


def explain_fund(fund, quote=None):
    total=(fund.get('holding_composition') or {}).get('total') or {}
    valuation=fund.get('return_valuation') or {}
    unrealized,realized,pnl=(total.get(k) for k in ('unrealized','realized','pnl'))
    return {'fund_id':fund['id'],'fund_code':fund.get('fund_code'),'name':fund['name'],
        'status':'READY' if all(v is not None for v in (unrealized,realized,pnl)) else 'PARTIAL',
        'basis':{k:valuation.get(k) for k in ('label','date','as_of','quote_id','value_kind','is_estimate','current_for_date')},
        'quote':{k:(quote or {}).get(k) for k in ('id','value','source','quality','as_of','observed_at','effective_date')},
        'components':dict(total,official_pnl=valuation.get('official_total_pnl'),mark_adjustment=valuation.get('mark_change')),
        'groups':(fund.get('holding_composition') or {}).get('groups') or {},
        'equation':'累计收益 = 当前市值 − 剩余成本 + 已实现收益',
        'price_notice':'当前价格重估只改变持有盈亏。正式累计收益加本次估值差额等于当前显示收益；今日变动参考不再重复相加。',
        'fee_notice':'买入费用已包含在成本，卖出费用已包含在净到账；净值中的管理等费用也已扣除，不再重复扣款。',
        'history_notice':'历史还原与自动正式净值入账按原标记保留，渠道实际校正优先。'}


def explain_portfolio(snapshot):
    p=snapshot['portfolio'];display=p.get('return_valuation') or p
    rows=[explain_fund(f) for f in snapshot['funds']]
    known=[dec(r['components']['pnl'],nonnegative=False) for r in rows if r['components'].get('pnl') is not None]
    complete=len(known)==len(rows)
    subtotal=sum(known,ZERO)
    total=display.get('pnl')
    residual=dec(total,nonnegative=False)-subtotal if total is not None and complete else None
    return {'scope':'PORTFOLIO','generated_at':snapshot['now'],'revision':snapshot['revision'],
        'total_pnl':total,'official_pnl':p.get('pnl'),'mark_adjustment':display.get('mark_change'),
        'fund_pnl':subtotal if complete else None,'known_fund_pnl':subtotal,'other_tracked_pnl':residual,
        'known_count':len(known),'fund_count':len(rows),'funds':rows,
        'label':display.get('label'),'equation':'总盈亏 = 各基金累计收益合计 + 其他跟踪项',
        'notice':'仅包含App内跟踪范围。其他跟踪项保留未分配分红或原现金簿等差额，不推定为个人现金余额。缺少基金成本或估值时只给已知小计。'}
