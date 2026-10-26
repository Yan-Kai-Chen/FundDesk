"""Economic BASE/T books; physical channel lots and redemption fees stay separate."""
from copy import deepcopy
from datetime import timedelta

from app.domain.finance import ZERO, dec, money, require, stamp, SHANGHAI, day

BUCKETS = {"BASE", "TACTICAL"}


def bucket(value):
    require(isinstance(value,str) and value in BUCKETS, "INVALID_BUCKET", "请选择底仓或做T")
    return value


def part(value, units, remaining):
    return None if value is None else value if units == remaining else money(value*units/remaining)


def choose(lots, units, intent, on):
    bucket(intent)
    left, result = dec(units,positive=True), []
    for source in (["TACTICAL","BASE"] if intent == "TACTICAL" else ["BASE"]):
        for lot in sorted(lots, key=lambda l:(l["at"],l["id"])):
            if lot["bucket"] != source or (lot.get("available_from") and lot["available_from"] > on):continue
            take = min(left,lot["units"]-lot["reserved"])
            if take > 0:
                result.append({"lot_id":lot["id"],"bucket":source,"units":take,
                               "cost":part(lot["cost"],take,lot["units"])})
                left -= take
            if not left:return result
    require(False,"BUCKET_UNITS_INSUFFICIENT",
            "底仓可用份额不足；做T卖出会先用T仓，再补扣底仓，不能超过两仓可用份额" if intent=="TACTICAL" else "底仓可用份额不足，底仓卖出不自动占用T仓")


class BucketBook:
    def __init__(self):
        self.lots, self.reservations, self.sales = {}, {}, []
        self.realized = {k:ZERO for k in BUCKETS}
        self.legacy_count = 0
        self.blocked = False

    def buy(self, id, units, cost, intent, at, available_from=None, t_units=None):
        bucket(intent);units=dec(units,positive=True)
        cost=None if cost is None else dec(cost)
        tactical = (units if t_units is None else dec(t_units)) if intent=="TACTICAL" else ZERO
        require(tactical <= units,"BUCKET_OVERALLOCATED","做T份额不能超过该笔买入总份额")
        if intent=="TACTICAL":require(tactical>0,"INVALID_AMOUNT","做T买入份额必须大于零")
        t_cost=part(cost,tactical,units) if tactical else ZERO
        for source,q,c in [("TACTICAL",tactical,t_cost),("BASE",units-tactical,None if cost is None else cost-t_cost)]:
            if q:
                key=id+"|"+source
                require(key not in self.lots,"DUPLICATE_LOT","分仓买入来源重复")
                self.lots[key]={"id":key,"source_id":id,"bucket":source,"units":q,"cost":c,
                                "reserved":ZERO,"at":at,"available_from":available_from}

    def reserve(self, id, units, intent, on):
        require(id not in self.reservations,"DUPLICATE_ORDER","分仓卖出来源重复")
        allocations=choose(list(self.lots.values()),units,intent,on)
        for a in allocations:self.lots[a['lot_id']]['reserved'] += a['units']
        self.reservations[id]={"intent":intent,"allocations":allocations}
        return allocations

    def release(self,id):
        order=self.reservations.pop(id,None)
        if order:
            for a in order['allocations']:self.lots[a['lot_id']]['reserved'] -= a['units']

    def sell(self,id,units,net,fee,at,pending=False):
        order=self.reservations[id]
        left=dec(units,positive=True);remaining_net=dec(net);remaining_fee=dec(fee)
        require(left<=sum((a['units'] for a in order['allocations']),ZERO),'BUCKET_UNITS_INSUFFICIENT','确认份额超过分仓申请的剩余份额')
        details=[];total_cost=ZERO;complete=True
        for a in order['allocations']:
            take=min(left,a['units'])
            if not take:continue
            lot=self.lots[a['lot_id']]
            cost=part(lot['cost'],take,lot['units'])
            received=part(remaining_net,take,left);allocated_fee=part(remaining_fee,take,left)
            if cost is None:complete=False
            else:lot['cost']-=cost;total_cost+=cost
            lot['units']-=take;lot['reserved']-=take;a['units']-=take
            details.append({'source_bucket':lot['bucket'],'units':take,'cost':cost,'net':received,'fee':allocated_fee})
            left-=take;remaining_net-=received;remaining_fee-=allocated_fee
            if not left:break
        pnl=dec(net)-total_cost if complete else None
        target=order['intent']
        self.realized[target]=(self.realized[target]+pnl if self.realized[target] is not None and pnl is not None else None)
        fallback=[d for d in details if target=='TACTICAL' and d['source_bucket']=='BASE']
        row={'id':id+':'+str(len(self.sales)),'order_id':id,'at':at,'intent':target,'units':dec(units),
             'net':dec(net),'fee':dec(fee),'cost':total_cost if complete else None,'pnl':pnl,'pending':pending,
             't_units':sum((d['units'] for d in details if d['source_bucket']=='TACTICAL'),ZERO),
             'base_units':sum((d['units'] for d in details if d['source_bucket']=='BASE'),ZERO),
             'fallback_units':sum((d['units'] for d in fallback),ZERO),
             'fallback_principal':None if any(d['cost'] is None for d in fallback) else sum((d['cost'] for d in fallback),ZERO),
             'allocations':details}
        self.sales.append(row)
        return row

    def summary(self,nav=None,on=None):
        if self.blocked:return {'status':'BLOCKED','notice':'历史含分红或拆分，分仓口径需另行核对；原账本和实际回填仍保留。'}
        total_units=sum((l['units'] for l in self.lots.values()),ZERO)
        total_value=money(total_units*dec(nav)) if nav is not None else ZERO if not total_units else None
        groups={}
        for source in ['BASE','TACTICAL']:
            lots=[l for l in self.lots.values() if l['bucket']==source and l['units']]
            units=sum((l['units'] for l in lots),ZERO)
            cost=None if any(l['cost'] is None for l in lots) else sum((l['cost'] for l in lots),ZERO)
            value=(ZERO if not units else None if total_value is None else
                   money(units*dec(nav)) if source=='BASE' else total_value-groups['BASE']['value'])
            floating=value-cost if value is not None and cost is not None else None
            realized=self.realized[source]
            groups[source]={'units':units,'reserved':sum((l['reserved'] for l in lots),ZERO),'cost':cost,'value':value,
                'available_units':sum((l['units']-l['reserved'] for l in lots if on is None or (l['available_from'] and l['available_from']<=on)),ZERO),
                'unrealized':floating,'realized':realized,'pnl':floating+realized if floating is not None and realized is not None else None}
        totals=[g['pnl'] for g in groups.values()]
        return {'status':'READY','groups':groups,'sales':self.sales,'positions':list(self.lots.values()),
                'total_pnl':sum(totals,ZERO) if all(v is not None for v in totals) else None,
                'total_units':total_units,'holding_value':total_value,'legacy_count':self.legacy_count,
                'policy':'T_FIRST_THEN_BASE_ALL_PNL_TO_T'}


def project_buckets(timeline):
    books,orders,dividends={}, {}, {}
    def book(fid):return books.setdefault(fid,BucketBook())
    for e in timeline:
        k,p=e['kind'],e['payload'];at=e['occurred_at']
        if k=='TRACKING_OPENING':
            b=book(p['fund_id'])
            require(not b.lots,'HISTORY_OVERLAP','分仓起点与已有记录重叠')
            for pos in p['economic_positions']:
                if dec(pos['units']):b.buy(e['id']+'|'+pos['id'],pos['units'],pos['cost'],pos['bucket'],at,stamp(at).astimezone(SHANGHAI).date().isoformat())
            b.realized={k:dec(p['bucket_realized'][k],nonnegative=False) for k in BUCKETS}
            b.sales=deepcopy(p['historical_sales'])
            s=b.summary(p['nav'],p['nav_date'])
            require(s['total_units']==dec(p['units']) and s['total_pnl']==dec(p['total_pnl'],nonnegative=False),
                    'OPENING_MISMATCH','分仓起点与历史合计不一致')
        elif k in {'SPLIT','DIVIDEND_DECLARED'}:
            book(p['fund_id']).blocked=True
            if k=='DIVIDEND_DECLARED':dividends[e['id']]=p['fund_id']
        elif k=='OPENING_LOT':
            b=book(p['fund_id'])
            if not b.blocked:
                b.buy(p.get('lot_id') or e['id'],p['units'],None if p.get('cost') in (None,'') else p['cost'],p.get('bucket','BASE'),at,p.get('available_from'))
                b.legacy_count += 'bucket' not in p
        elif k=='ORDER_SUBMITTED':
            intent=bucket(p.get('bucket','BASE'));orders[p['id']]=dict(p,bucket=intent)
            b=book(p['fund_id']);b.legacy_count += 'bucket' not in p
            if p['side']=='SELL' and not b.blocked:
                b.reserve(p['id'],p['requested'],intent,stamp(at).astimezone(SHANGHAI).date().isoformat())
        elif k=='ORDER_CONFIRMED':
            o=orders[p['order_id']];b=book(o['fund_id'])
            if b.blocked:continue
            if o['side']=='BUY':b.buy(p.get('lot_id') or e['id'],p['units'],p['gross'],o['bucket'],at,p.get('available_from'))
            else:
                b.sell(o['id'],p['units'],p['net'],p['fee'],at)
                if p.get('is_final',True):b.release(o['id'])
        elif k in {'ORDER_CANCELED','ORDER_REJECTED'}:
            o=orders[p['order_id']]
            if o['side']=='SELL':book(o['fund_id']).release(o['id'])
    return books


def reference_buckets(ref,assignments):
    require(isinstance(assignments,list),'INVALID_BUCKET_CONFIG','请逐笔指定底仓或做T')
    all_rows={r['id']:r for r in ref['buys']+ref['sells']}
    mapping={}
    for row in assignments:
        require(isinstance(row,dict) and isinstance(row.get('row_id'),str) and row['row_id'] in all_rows,
                'BUCKET_SOURCE_MISSING','分仓引用的交易不存在')
        require(row['row_id'] not in mapping,'BUCKET_DUPLICATE','每笔交易只设置一次归属')
        bucket(row.get('bucket'));mapping[row['row_id']]=row
    require(set(mapping)==set(all_rows),'BUCKET_CONFIG_INCOMPLETE','请为全部历史买卖选择归属')
    buys={r['id'] for r in ref['buys']};b=BucketBook();normalized=[]
    for r in sorted(all_rows.values(),key=lambda r:(r['at'],r['id'])):
        setting=mapping[r['id']];intent=setting['bucket'];clean={'row_id':r['id'],'bucket':intent}
        if r['id'] in buys:
            t_units=dec(setting['t_units'],positive=True) if intent=='TACTICAL' and setting.get('t_units') not in (None,'') else None
            if t_units is not None:
                require(t_units % dec('0.01')==0,'PAIRING_PRECISION','截图分仓份额最多两位小数');clean['t_units']=t_units
            available=(day(r['at'][:10])+timedelta(days=1)).isoformat()
            b.buy(r['id'],r['units'],r['cost'],intent,r['at'],available,t_units)
        else:
            b.reserve(r['id'],r['units'],intent,r['at'][:10]);b.sell(r['id'],r['units'],r['net'],r['fee'],r['at'],r['pending']);b.release(r['id'])
        normalized.append(clean)
    result=b.summary(ref['nav'],ref['nav_date'])
    require(result['total_pnl']==ref['total_pnl'],'ATTRIBUTION_INCONSISTENT','分仓盈亏未能与基金总盈亏核对')
    return dict(result,assignments=normalized,buys=ref['buys'],sells=ref['sells'],nav=ref['nav'],nav_date=ref['nav_date'])
