"""Reference stock sessions, not a fund subscription calendar. All times Shanghai.

2026 full-day holidays: SSE 2025-12-22 announcement and HKEX CT/075/25.
HKEX half-days follow the published eve-of-holiday trading-hours rule.
Unknown years/markets stay unknown; government make-up workdays stay weekends.
"""
from datetime import date, timedelta, time

from app.domain.finance import SHANGHAI, stamp

CALENDAR_VERSION = "reference-2026-v1"
CALENDAR_SOURCES = [
    "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml",
    "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2025/ce_SEHK_CT_075_2025.pdf",
    "https://www.hkex.com.hk/Services/Trading-hours-and-Severe-Weather-Arrangements/Trading-Hours/Securities-Market",
]


def _days(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return {(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)}


CN_HOLIDAYS = set().union(*[_days(a, b) for a, b in [
    ("2026-01-01", "2026-01-03"), ("2026-02-15", "2026-02-23"),
    ("2026-04-04", "2026-04-06"), ("2026-05-01", "2026-05-05"),
    ("2026-06-19", "2026-06-21"), ("2026-09-25", "2026-09-27"),
    ("2026-10-01", "2026-10-07"),
]])
HK_HOLIDAYS = {"2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19",
               "2026-04-03", "2026-04-06", "2026-04-07", "2026-05-01",
               "2026-05-25", "2026-06-19", "2026-07-01", "2026-10-01",
               "2026-10-19", "2026-12-25"}
HK_HALF_DAYS = {"2026-02-16", "2026-12-24", "2026-12-31"}


def venues(scope):
    return {"CN": ["CN"], "CN_HK": ["CN", "HK"], "GOLD_CN": ["SGE"]}.get(scope, [])


def open_day(d, venue):
    return d.year == 2026 and d.weekday() < 5 and d.isoformat() not in (CN_HOLIDAYS if venue in {"CN", "SGE"} else HK_HOLIDAYS)


def previous_reference_date(d, scope):
    markets = venues(scope)
    for i in range(1, 20):
        previous = d - timedelta(days=i)
        if previous.year != 2026 or not markets:
            return None
        if any(open_day(previous, v) for v in markets):
            return previous.isoformat()
    return None


def market_session(at, scope):
    local = stamp(at).astimezone(SHANGHAI)
    d, t = local.date(), local.time()
    markets = venues(scope)
    base = {"date": d.isoformat(), "timezone": "Asia/Shanghai", "scope": scope,
            "calendar_version": CALENDAR_VERSION, "sources": CALENDAR_SOURCES,
            "previous_date": previous_reference_date(d, scope), "open_venues": [],
            "partial_holiday": False, "before_cutoff": t < time(15)}
    if scope == 'GOLD_CN':
        base['calendar_version'] = 'sge-day-2026-v1'
        base['sources'] = [
            'https://www.sge.com.cn/h5_sjzx/wzxq/10007108?topic=jjsnotice',
            'https://www.sge.com.cn/cpfw/Product_service_details?cplx=7&parent_cplx=0&pro_id=793730879941324800',
            'https://www.sge.com.cn/cpfw/cpdj']
    if d.year != 2026 or not markets:
        return dict(base, status="UNKNOWN", label="日历待核实", detail="参考市场或年份尚未核实")
    day_markets = [v for v in markets if open_day(d, v)]
    if not day_markets:
        return dict(base, status="NOT_OPEN", label="没开盘", detail="参考市场今日休市")
    base["partial_holiday"] = len(day_markets) != len(markets)
    sessions = {"CN": [(time(9, 30), time(11, 30)), (time(13), time(15))], "SGE": [(time(9), time(15, 30))],
                "HK": [(time(9, 30), time(12))] + ([] if d.isoformat() in HK_HALF_DAYS else [(time(13), time(16, 10))])}
    # Half-day closing auction can run until 12:10.
    if d.isoformat() in HK_HALF_DAYS:
        sessions["HK"] = [(time(9, 30), time(12, 10))]
    active = [v for v in day_markets if any(a <= t < b for a, b in sessions[v])]
    base["open_venues"] = active
    if active:
        return dict(base, status="OPEN", label="交易中", detail=" / ".join({'CN': 'A股', 'HK': '港股', 'SGE': '上海黄金交易所 · 日间参考'}[v] for v in active))
    opening = time(9) if scope == 'GOLD_CN' else time(9, 30)
    if t < opening:
        return dict(base, status="NOT_OPEN", label="没开盘", detail=f"北京时间 {opening.strftime('%H:%M')} 开盘")
    end = max(sessions[v][-1][1] for v in day_markets)
    if t >= end:
        return dict(base, status="CLOSED", label="已收盘", detail="今日参考市场交易结束")
    return dict(base, status="BREAK", label="午间休市", detail="北京时间 13:00 继续交易")
