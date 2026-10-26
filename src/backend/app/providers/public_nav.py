"""Read the JSON literals in a public fund chart file, without executing scripts."""
from datetime import datetime
from decimal import Decimal
from time import time_ns
from urllib.parse import urlencode
import hashlib
import json
import re
import httpx

from app.domain.finance import SHANGHAI, Problem, dec, day, require

SUPPORTED = {"012183": "广发沪港深精选混合C", "021528": "财通成长优选混合C", "008987": "广发上海金ETF联接C",
             "012738": "广发创新药ETF联接C", "019633": "国泰半导体设备ETF联接C", "017954": "汇添富中证1000指数增强C",
             "006810": "泰康香港银行指数C"}
MARKET_SCOPES = {"012183": "CN_HK", "021528": "CN", "008987": "GOLD_CN",
                 "012738": "CN", "019633": "CN", "017954": "CN_HK", "006810": "CN_HK"}
EQUITY_ESTIMATE_CODES = frozenset(SUPPORTED) - {'008987'}
SOURCE_NAME = "天天基金公开披露净值"
LATEST_SOURCE = "https://api.fund.eastmoney.com/f10/lsjz"


def source_url(code):
    require(code in SUPPORTED, "SOURCE_NOT_SUPPORTED", "该基金尚未核实公开净值来源")
    return f"https://fund.eastmoney.com/pingzhongdata/{code}.js"


def latest_url(code):
    source_url(code)
    return LATEST_SOURCE + '?' + urlencode({'fundCode': code, 'pageIndex': 1, 'pageSize': 5})


def parse_latest_nav(body, code, today):
    """Small disclosure page; identity is anchored to the code/name history below."""
    latest_url(code)
    require(len(body) <= 65536, 'SOURCE_TOO_LARGE', '最新净值响应超过64KB')
    try:
        obj = json.loads(body, parse_float=Decimal,
                         parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        require(isinstance(obj, dict) and obj.get('ErrCode') == 0, 'SOURCE_FORMAT_CHANGED', '最新净值接口返回错误')
        rows = obj.get('Data', {}).get('LSJZList')
        require(isinstance(rows, list) and 1 <= len(rows) <= 5, 'SOURCE_FORMAT_CHANGED', '最新净值列表无效')
        result, previous = [], None
        for row in rows:
            require(isinstance(row, dict), 'SOURCE_FORMAT_CHANGED', '最新净值记录无效')
            require(row.get('FCODE', code) == code, 'SOURCE_IDENTITY_MISMATCH', '最新净值份额代码不符')
            effective = day(row['FSRQ']).isoformat()
            require(effective <= today and (previous is None or effective < previous),
                    'SOURCE_DATE_INVALID', '最新净值重复、倒序或未来日期')
            require(row.get('NAVTYPE') == '1' and 'FHSP' in row, 'SOURCE_FORMAT_CHANGED', '净值类型或分红字段变化')
            action = '；'.join(dict.fromkeys(str(row.get(k) or '').strip() for k in
                              ('FHSP', 'FHFCBZ', 'FHFCZ', 'FHFCZ10'))).strip('；')
            require(len(action) <= 200, 'SOURCE_FORMAT_CHANGED', '分红拆分字段异常')
            result.append({'effective_date': effective, 'value': dec(row['DWJZ'], positive=True),
                           'corporate_action': action})
            previous = effective
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError):
        raise Problem('SOURCE_FORMAT_CHANGED', '最新净值不是有效的JSON披露数据')
    return list(reversed(result))


def _merge_latest(history, recent, body):
    old = {r['effective_date']: r for r in history['records']}
    # An old cached response must never roll back a newer history file.
    if recent[-1]['effective_date'] < history['records'][-1]['effective_date']:
        return history
    anchor_end = min(recent[-1]['effective_date'], history['records'][-1]['effective_date'])
    anchors = [r for r in recent if r['effective_date'] in old and r['effective_date'] < anchor_end]
    require(anchors and all(r['value'] == old[r['effective_date']]['value'] for r in anchors),
            'SOURCE_IDENTITY_MISMATCH', '最新净值与已核实同份额历史不能对齐')
    for row in recent:
        existing = old.get(row['effective_date'])
        # Never discard a known action because one endpoint omitted its text.
        old[row['effective_date']] = dict(row, corporate_action=(row['corporate_action'] or
            (existing or {}).get('corporate_action', '')))
    evidence = json.dumps({'history': history['raw_body'].decode('utf-8-sig'),
                           'latest': body.decode('utf-8-sig')}, ensure_ascii=False).encode()
    return dict(history, records=[old[k] for k in sorted(old)], source_url=latest_url(history['fund_code']),
                history_source_url=history['source_url'], freshness_source='EASTMONEY_HISTORY_API',
                raw_sha256=hashlib.sha256(evidence).hexdigest(), raw_body=evidence)


def literal(text, name):
    matches = list(re.finditer(r"\bvar\s+" + re.escape(name) + r"\s*=\s*", text))
    require(len(matches) == 1, "SOURCE_FORMAT_CHANGED", "公开净值字段缺失或重复")
    value_text = text[matches[0].end():]
    try:
        value, end = json.JSONDecoder(parse_float=Decimal,
            parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x))).raw_decode(value_text)
    except (ValueError, RecursionError):
        raise Problem("SOURCE_FORMAT_CHANGED", "公开净值字段不再是合法 JSON 字面值")
    require(value_text[end:].lstrip().startswith(";"), "SOURCE_FORMAT_CHANGED", "公开净值字段包含非数据表达式")
    return value


def parse_public_nav(body, code, today, since="2026-07-01"):
    source_url(code)
    require(len(body) <= 2_000_000, "SOURCE_TOO_LARGE", "公开净值响应超过 2 MB")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError:
        raise Problem("SOURCE_FORMAT_CHANGED", "公开净值编码无效")
    require(literal(text, "fS_code") == code and literal(text, "fS_name") == SUPPORTED[code],
            "SOURCE_IDENTITY_MISMATCH", "来源代码或份额类别不符，未写入行情")
    records = literal(text, "Data_netWorthTrend")
    require(isinstance(records, list) and 20 <= len(records) <= 20_000,
            "SOURCE_FORMAT_CHANGED", "公开净值历史结构或数量异常")
    day(today); day(since)
    result, previous = [], ""
    for row in records:
        require(isinstance(row, dict) and isinstance(row.get("x"), int), "SOURCE_FORMAT_CHANGED", "净值日期结构无效")
        try:
            effective = datetime.fromtimestamp(row["x"] / 1000, SHANGHAI).date().isoformat()
        except (ValueError, OverflowError, OSError):
            raise Problem("SOURCE_FORMAT_CHANGED", "净值时间戳无效")
        require(effective > previous and effective <= today, "SOURCE_DATE_INVALID", "净值日期重复、倒序或晚于当前日期")
        previous = effective
        if effective >= since:
            # Chart files sometimes contain binary-float artifacts years before
            # our requested window. Do not round or validate unused prices as
            # though they were financial inputs to this import.
            result.append({"effective_date": effective, "value": dec(row.get("y"), positive=True),
                           "corporate_action": str(row.get("unitMoney") or "")[:200]})
    require(bool(result), "SOURCE_EMPTY", "所需区间没有公开净值")
    return {"fund_code": code, "name": SUPPORTED[code], "records": result,
            "source": SOURCE_NAME, "source_url": source_url(code),
            "raw_sha256": hashlib.sha256(body).hexdigest()}


def fetch_public_nav(code, today, *, client=None):
    url = source_url(code)
    owned = client is None
    client = client or httpx.Client(timeout=5, follow_redirects=False)
    try:
        # The bare chart URL can remain cached for hours while the current
        # disclosure API is already updated. One nonce per scheduled request
        # avoids that cached URL; it does not increase the ten-minute cadence.
        with client.stream("GET", url + '?v=' + str(time_ns()), timeout=5, follow_redirects=False,
                           headers={'Cache-Control': 'no-cache'}) as response:
            require(response.status_code != 429, "RATE_LIMITED", "上游限流，请稍后再试")
            require(response.status_code == 200, "SOURCE_UNAVAILABLE", "公开净值来源暂不可用；保留已有数据")
            require(any(t in response.headers.get("content-type", "").lower()
                        for t in ("javascript", "text/plain")), "SOURCE_FORMAT_CHANGED", "公开数据返回了意外格式")
            chunks, size = [], 0
            for chunk in response.iter_bytes(chunk_size=65536):
                size += len(chunk)
                require(size <= 2_000_000, "SOURCE_TOO_LARGE", "公开净值响应超过 2 MB")
                chunks.append(chunk)
        body = b"".join(chunks)
        history = dict(parse_public_nav(body, code, today), raw_body=body)
        try:
            with client.stream('GET', latest_url(code) + '&_=' + str(time_ns()), timeout=5, follow_redirects=False,
                               headers={'Referer': 'https://fundf10.eastmoney.com/', 'Cache-Control': 'no-cache'}) as r:
                require(r.status_code != 429, 'RATE_LIMITED', '最新净值接口限流')
                require(r.status_code == 200, 'SOURCE_UNAVAILABLE', '最新净值接口暂不可用')
                chunks, size = [], 0
                for chunk in r.iter_bytes(chunk_size=16384):
                    size += len(chunk)
                    require(size <= 65536, 'SOURCE_TOO_LARGE', '最新净值响应超过64KB')
                    chunks.append(chunk)
            recent_body = b''.join(chunks)
            recent = parse_latest_nav(recent_body, code, today)
            return _merge_latest(history, recent, recent_body)
        except (Problem, httpx.HTTPError) as exc:
            # Keep a valid history when the supplementary endpoint is down;
            # expose the check failure without promoting an estimate to NAV.
            return dict(history, fallback_errors=[{'provider': 'EASTMONEY_HISTORY_API',
                                                   'code': getattr(exc, 'code', 'SOURCE_UNAVAILABLE')}])
    except (httpx.TimeoutException, httpx.NetworkError):
        raise Problem("SOURCE_UNAVAILABLE", "公开净值来源连接超时或网络故障；保留已有数据")
    finally:
        if owned:
            client.close()
