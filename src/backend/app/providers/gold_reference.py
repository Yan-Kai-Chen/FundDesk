"""SGE CNY gold reference for 008987 C. A local approximation, never NAV.

Before today's SHAU PM fixing, use the genuine delayed Au99.99 day quote.
The PM fixing is date-only: keep its first receipt time, never invent a quote
publication timestamp. Source arrays contain future placeholders; do not use
their last element as the current price.
"""
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re

import httpx

from app.domain.finance import Problem, SHANGHAI, dec, require, stamp
from app.domain.market_calendar import market_session

MODEL_VERSION = 'shau-gold-v1'
PROVIDER = 'SGE_GOLD_REFERENCE'
WEIGHT = Decimal('0.95')
FIXING_URL = 'https://www.sge.com.cn/graph/DayilyJzj'
SPOT_URL = 'https://www.sge.com.cn/graph/quotations'
BENCHMARK_URL = 'https://www.gffunds.com.cn/funds/cpmp/202006/t20200623_358715.shtml'
LIMIT = 512_000


def _json(body):
    require(len(body) <= LIMIT, 'SOURCE_TOO_LARGE', '金价响应超过大小限制')
    try:
        return json.loads(body.decode('utf-8-sig'), parse_float=Decimal,
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (UnicodeError, ValueError, RecursionError):
        raise Problem('SOURCE_FORMAT_CHANGED', '黄金交易所响应格式无效')


def parse_pm_fixings(body, now):
    data = _json(body)
    require(isinstance(data, dict) and isinstance(data.get('wp'), list) and 1 <= len(data['wp']) <= 5000,
            'SOURCE_FORMAT_CHANGED', '上海金午盘基准价结构变化')
    today = stamp(now).astimezone(SHANGHAI).date().isoformat()
    result, previous = {}, ''
    for row in data['wp']:
        require(isinstance(row, list) and len(row) == 2 and type(row[0]) is int,
                'SOURCE_FORMAT_CHANGED', '上海金基准日期结构无效')
        try:
            at = datetime.fromtimestamp(row[0] / 1000, SHANGHAI)
        except (ValueError, OverflowError, OSError):
            raise Problem('SOURCE_FORMAT_CHANGED', '上海金基准日期无效')
        day = at.date().isoformat()
        require(at.time() == time(0) and previous < day <= today,
                'SOURCE_DATE_INVALID', '上海金基准日期重复、倒序或来自未来')
        result[day] = dec(row[1], positive=True)
        previous = day
    return result


def parse_spot(body, now):
    data = _json(body)
    require(isinstance(data, dict) and data.get('heyue') == 'Au99.99',
            'SOURCE_IDENTITY_MISMATCH', '黄金合约须为人民币 Au99.99')
    try:
        at = datetime.strptime(data['delaystr'], '%Y年%m月%d日 %H:%M:%S').replace(tzinfo=SHANGHAI)
    except (KeyError, TypeError, ValueError):
        raise Problem('SOURCE_FORMAT_CHANGED', '金价缺少有效来源日期时间')
    local = stamp(now).astimezone(SHANGHAI)
    require(at.date() == local.date(), 'QUOTE_WRONG_DATE', '黄金来源不是今日报价')
    age = (local - at).total_seconds()
    close_clock = time(15, 30) <= at.time() <= time(15, 45)
    require(age >= 0 and (age <= 900 or close_clock), 'QUOTE_STALE', '金价超过15分钟或时点来自未来')
    times, prices = data.get('times'), data.get('data')
    require(isinstance(times, list) and isinstance(prices, list) and len(times) == len(prices)
            and 1 <= len(times) <= 1440, 'SOURCE_FORMAT_CHANGED', '黄金分时序列结构变化')
    eligible, seen = [], set()
    for minute, price in zip(times, prices):
        require(isinstance(minute, str) and re.fullmatch(r'\d{2}:\d{2}', minute) and minute not in seen,
                'SOURCE_FORMAT_CHANGED', '黄金分时时间无效或重复')
        seen.add(minute)
        try:
            point = datetime.combine(at.date(), time.fromisoformat(minute), SHANGHAI)
        except ValueError:
            raise Problem('SOURCE_FORMAT_CHANGED', '黄金分时时间无效')
        # Night prices have a different calendar date; future day entries can
        # be yesterday's placeholders. Neither is today's observed day price.
        if time(9) <= point.time() <= time(15, 30) and point <= at:
            eligible.append((point, dec(price, positive=True)))
    require(bool(eligible), 'QUOTE_MISSING', '暂无当日黄金日间报价')
    point, price = max(eligible)
    close = close_clock and point.time() == time(15, 30)
    require(0 <= (at - point).total_seconds() and (close or (at - point).total_seconds() < 120), 'QUOTE_STALE', '黄金分时点未跟上来源时钟')
    require(age <= 900 or close, 'QUOTE_STALE', '缺少有效黄金日间收盘点')
    return {'value': price, 'as_of': point.isoformat(), 'source_clock': at.isoformat(), 'closing_reference': close}


def historical_deviation(history, fixings, today):
    rows = [r for r in history if r['effective_date'] < today][-61:]
    errors = []
    for a, b in zip(rows, rows[1:]):
        x, y = a['effective_date'], b['effective_date']
        if x in fixings and y in fixings:
            errors.append(abs(dec(b['value']) / dec(a['value']) - 1 - WEIGHT * (fixings[y] / fixings[x] - 1)))
    return {'sample_count': len(errors), 'mean_absolute_deviation': sum(errors) / len(errors) if len(errors) >= 20 else None,
            'basis': '严格使用今日之前已披露净值与同日午盘基准；历史偏差不是预测区间'}


def build_reference(code, history, fixing_body, spot_body, now):
    require(code == '008987', 'SOURCE_NOT_SUPPORTED', '金价模型只核实了008987 C')
    session = market_session(now, 'GOLD_CN')
    require(session['status'] in {'OPEN', 'CLOSED'}, 'OUTSIDE_SESSION', '黄金日间参考市场未交易')
    today = session['date']
    prior = [r for r in history if r['effective_date'] < today]
    require(prior and prior[-1]['effective_date'] == session['previous_date'],
            'QUOTE_BASIS_MISMATCH', '先更新上一黄金交易日正式净值')
    baseline = prior[-1]
    fixings = parse_pm_fixings(fixing_body, now)
    previous = fixings.get(baseline['effective_date'])
    require(previous is not None, 'QUOTE_BASIS_MISMATCH', '缺少与基金基准日一致的上海金午盘价')
    fixed = today in fixings
    if fixed:
        require(stamp(now).astimezone(SHANGHAI).time() >= time(14, 15),
                'QUOTE_FUTURE', '午盘集中定价尚未开始，拒绝今日基准价')
        price, as_of = fixings[today], None
        source, mode = '上海金午盘基准 · 本地参考估算', 'TODAY_PM_FIXING'
        spot = None
    else:
        require(spot_body is not None, 'QUOTE_MISSING', '午盘基准未公布，等待日间金价')
        spot = parse_spot(spot_body, now)
        price, as_of = spot['value'], spot['as_of']
        source, mode = (('上金所 Au99.99 收盘 · 本地参考估算', 'DAY_CLOSE_PROXY') if spot['closing_reference'] else
                        ('上金所 Au99.99 · 本地参考估算', 'LIVE_SPOT_PROXY'))
    change = WEIGHT * (price / previous - 1)
    require(Decimal('-0.5') <= change <= Decimal('0.5'), 'QUOTE_OUTLIER', '金价参考变化异常，需核对来源')
    # Observe's shared numeric contract accepts at most 12 decimal places.
    # A division by a real fixing usually repeats; quantize only the final
    # derived NAV, never the raw gold price or the official NAV baseline.
    value = (dec(baseline['value']) * (1 + change)).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
    evidence = {'model_version': MODEL_VERSION, 'reference_mode': mode, 'fixed_for_date': fixed,
        'fixing_date': today if fixed else None, 'gold_price': price, 'gold_unit': 'CNY_PER_GRAM',
        'previous_pm_date': baseline['effective_date'], 'previous_pm_price': previous,
        'gold_weight': WEIGHT, 'fixing_source_url': FIXING_URL, 'spot_source_url': None if fixed else SPOT_URL,
        'nav_rounding': '0.00000001 / ROUND_HALF_UP',
        'benchmark_source_url': BENCHMARK_URL, 'source_clock': spot['source_clock'] if spot else None,
        'historical_deviation': historical_deviation(history, fixings, today),
        'limitations': '95%为业绩基准权重；未计现金利息、当日费用及跟踪差异。金价不是基金成交价或正式净值。'}
    # Original responses are durable by hash only when referenced; rotating
    # cache policy applies. No timestamp is added to a date-only fixing.
    raw = json.dumps({'fixings': fixing_body.decode('utf-8-sig'),
                      'spot': None if fixed else spot_body.decode('utf-8-sig')}, ensure_ascii=False, separators=(',', ':')).encode()
    return {'provider_id': PROVIDER, 'source': source, 'source_url': FIXING_URL if fixed else SPOT_URL,
        'date': today, 'reference_date': baseline['effective_date'], 'reference_nav': dec(baseline['value']),
        'records': [{'effective_date': today, 'as_of': as_of, 'value': value}],
        'value_kind': 'LOCAL_ESTIMATE', 'quality': 'LOCAL_GOLD_REFERENCE', 'evidence': evidence,
        'raw_sha256': hashlib.sha256(raw).hexdigest(), 'raw_body': raw}


def _download(client, method, url, data):
    try:
        with client.stream(method, url, data=data, timeout=5, follow_redirects=False,
                           headers={'Referer': 'https://www.sge.com.cn/', 'User-Agent': 'Mozilla/5.0'}) as response:
            require(response.status_code != 429, 'RATE_LIMITED', '黄金来源限流')
            require(response.status_code == 200, 'SOURCE_UNAVAILABLE', '黄金来源暂不可用')
            parts, size = [], 0
            for part in response.iter_bytes(chunk_size=16384):
                size += len(part)
                require(size <= LIMIT, 'SOURCE_TOO_LARGE', '黄金响应超过大小限制')
                parts.append(part)
            return b''.join(parts)
    except httpx.HTTPError:
        raise Problem('SOURCE_UNAVAILABLE', '黄金来源连接失败，保留上次数据和时间')


def fetch_gold_reference(code, now, history, *, client=None):
    require(code == '008987', 'SOURCE_NOT_SUPPORTED', '金价模型只核实了008987 C')
    owned = client is None
    client = client or httpx.Client(timeout=5, follow_redirects=False)
    try:
        fixings = _download(client, 'POST', FIXING_URL, {})
        current = now()
        fixed = stamp(current).astimezone(SHANGHAI).date().isoformat() in parse_pm_fixings(fixings, current)
        spot = None if fixed else _download(client, 'GET', SPOT_URL, {'instid': 'Au99.99'})
        return build_reference(code, history, fixings, spot, now())
    finally:
        if owned:
            client.close()
