"""Bounded public data adapters. Parse literals only; never execute source JS.

THS endpoints are taken from its public fund-detail page scripts. The website's
estimate display switch is off; the unauthenticated minute-data endpoint still
returns quotes. Treat it as an undocumented third-party estimate, not an SLA or
manager-confirmed NAV. Validate its date and reference NAV before using it.
"""
from datetime import datetime, time
from decimal import Decimal
import hashlib
import json
import re

import httpx

from app.domain.finance import Problem, SHANGHAI, dec, day, require, stamp
from app.providers.public_nav import SUPPORTED, EQUITY_ESTIMATE_CODES, fetch_public_nav
from app.domain.quote_phase import EQUITY_CLOSE_PROVIDERS, morning_reference

THS_SOURCE = "同花顺公开接口 · 盘中估算"


def supported(code):
    require(code in SUPPORTED, "SOURCE_NOT_SUPPORTED", "该基金尚未核实自动行情来源")


def _download(url, *, client=None, limit=131072):
    owned = client is None
    client = client or httpx.Client(timeout=5, follow_redirects=False)
    try:
        with client.stream("GET", url, timeout=5, follow_redirects=False,
                           headers={"Referer": "https://fund.10jqka.com.cn/"}) as r:
            require(r.status_code != 429, "RATE_LIMITED", "行情来源限流，本轮停止重试")
            require(r.status_code == 200, "SOURCE_UNAVAILABLE", "行情来源暂不可用，保留上次数据")
            chunks, size = [], 0
            for chunk in r.iter_bytes(chunk_size=16384):
                size += len(chunk)
                require(size <= limit, "SOURCE_TOO_LARGE", "行情响应超过大小限制")
                chunks.append(chunk)
        return b"".join(chunks)
    except httpx.HTTPError:
        raise Problem("SOURCE_UNAVAILABLE", "行情连接超时或网络故障，保留上次数据")
    finally:
        if owned:
            client.close()


def _text(body):
    require(len(body) <= 131072, "SOURCE_TOO_LARGE", "行情响应超过大小限制")
    try:
        return body.decode("utf-8-sig")
    except UnicodeError:
        raise Problem("SOURCE_FORMAT_CHANGED", "行情响应编码无效")


def ths_estimate_url(code):
    supported(code)
    return f"https://gz-fund.10jqka.com.cn/?module=api&controller=index&action=chart&info=vm_fd_{code}&start=0930"


def _validate_quote(data, now):
    local = stamp(now).astimezone(SHANGHAI)
    require(data["date"] == local.date().isoformat(), "QUOTE_WRONG_DATE", "来源没有返回今天的估值")
    require(data["reference_date"] < data["date"], "QUOTE_BASIS_INVALID", "估值基准日期无效")
    latest = stamp(data["records"][-1]["as_of"])
    close = (data['provider_id'] in EQUITY_CLOSE_PROVIDERS and
             latest.astimezone(SHANGHAI).time() == time(15) and local.time() >= time(15))
    morning = data['provider_id'] in EQUITY_CLOSE_PROVIDERS and morning_reference(latest.isoformat(), now)
    age = (stamp(now) - latest).total_seconds()
    require(0 <= age and (age <= 900 or close or morning),
            "QUOTE_STALE", "来源估值超过 15 分钟或时点晚于当前时间")
    for row in data["records"]:
        require(stamp(row["as_of"]) <= stamp(now), "QUOTE_FUTURE", "来源含未来时点")
        require(Decimal("0.5") <= row["value"] / data["reference_nav"] <= Decimal("1.5"),
                "QUOTE_OUTLIER", "估值相对基准变化异常，需核对来源或份额口径")
    return dict(data, closing_reference=close, morning_reference=morning)


def parse_ths_estimate(body, code, now):
    supported(code)
    text = _text(body)
    match = re.fullmatch(r"\s*vm_fd_" + code + r"\s*=\s*'([0-9.,:;|~\-]+)'\s*;?\s*", text)
    require(match is not None, "SOURCE_FORMAT_CHANGED", "同花顺分时数据格式或基金代码不符")
    try:
        header, values = match[1].split("|")
        reference_date, sessions = header.split(";")
        date_, reference, points = values.split("~")
        day(date_); day(reference_date)
        require(sessions == "0930-1130,1300-1500", "SOURCE_FORMAT_CHANGED", "分时时段协议变化")
        reference = dec(reference, positive=True)
        records, previous = [], ""
        for point in points.split(";"):
            minute, value, repeated_base, _ = point.split(",")
            require(re.fullmatch(r"\d{4}", minute) and minute > previous,
                    "SOURCE_FORMAT_CHANGED", "分时时间重复或倒序")
            at = datetime.combine(day(date_), time(int(minute[:2]), int(minute[2:])), SHANGHAI)
            require(time(9, 30) <= at.time() <= time(11, 30) or time(13) <= at.time() <= time(15),
                    "SOURCE_FORMAT_CHANGED", "分时记录不在来源声明的时段内")
            require(dec(repeated_base, positive=True) == reference, "QUOTE_BASIS_INVALID", "来源基准净值不一致")
            records.append({"as_of": at.isoformat(), "effective_date": date_, "value": dec(value, positive=True)})
            previous = minute
        require(1 <= len(records) <= 242, "SOURCE_FORMAT_CHANGED", "分时记录数量异常")
    except (ValueError, OverflowError):
        raise Problem("SOURCE_FORMAT_CHANGED", "同花顺分时字段无效")
    data = {"provider_id": "THS_PUBLIC_ESTIMATE", "source": THS_SOURCE, "source_url": ths_estimate_url(code),
            "date": date_, "reference_date": reference_date, "reference_nav": reference, "records": records,
            "raw_sha256": hashlib.sha256(body).hexdigest(), "raw_body": body}
    return _validate_quote(data, now)


def parse_fundgz(body, code, now):
    supported(code)
    match = re.fullmatch(r"\s*jsonpgz\((\{.*\})\);?\s*", _text(body), re.S)
    require(match is not None, "SOURCE_FORMAT_CHANGED", "备用估值来源没有返回数据字面量")
    try:
        obj = json.loads(match[1], parse_float=Decimal,
                         parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        require(obj.get("fundcode") == code and obj.get("name", "").replace(" ", "") == SUPPORTED[code],
                "SOURCE_IDENTITY_MISMATCH", "备用估值代码或份额类别不符")
        at = datetime.strptime(obj["gztime"], "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
        date_, reference_date = at.date().isoformat(), day(obj["jzrq"]).isoformat()
        reference, value = dec(obj["dwjz"], positive=True), dec(obj["gsz"], positive=True)
    except (KeyError, ValueError, TypeError, RecursionError):
        raise Problem("SOURCE_FORMAT_CHANGED", "备用估值字段无效")
    data = {"provider_id": "EASTMONEY_PUBLIC_ESTIMATE", "source": "天天基金公开接口 · 盘中估算",
            "source_url": f"https://fundgz.1234567.com.cn/js/{code}.js", "date": date_,
            "reference_date": reference_date, "reference_nav": reference,
            "records": [{"as_of": at.isoformat(), "effective_date": date_, "value": value}],
            "raw_sha256": hashlib.sha256(body).hexdigest(), "raw_body": body}
    return _validate_quote(data, now)


def fetch_estimate(code, now, *, client=None, allow_fallback=True):
    supported(code)
    require(code in EQUITY_ESTIMATE_CODES, 'SOURCE_NOT_SUPPORTED', '该基金不使用股票基金第三方分时来源')
    clock = now if callable(now) else lambda: now
    try:
        body = _download(ths_estimate_url(code), client=client)
        return dict(parse_ths_estimate(body, code, clock()), fallback_checked=False, attempts=[])
    except Problem as first:
        if first.code == "RATE_LIMITED" or not allow_fallback:
            raise
        try:
            body = _download(f"https://fundgz.1234567.com.cn/js/{code}.js", client=client)
            return dict(parse_fundgz(body, code, clock()), fallback_checked=True,
                        attempts=[{"provider": "THS_PUBLIC_ESTIMATE", "code": first.code}])
        except Problem as second:
            exc = Problem("ESTIMATE_UNAVAILABLE", "主备盘中估值均不可用，保留上次报价")
            exc.provider_errors = [{"provider": "THS_PUBLIC_ESTIMATE", "code": first.code},
                                   {"provider": "EASTMONEY_PUBLIC_ESTIMATE", "code": second.code}]
            exc.fallback_checked = True
            raise exc


def fetch_ths_nav(code, today, *, client=None, since="2026-07-01"):
    supported(code)
    url = f"https://fund.10jqka.com.cn/{code}/json/jsondwjz.json"
    body = _download(url, client=client)
    match = re.fullmatch(r"\s*var\s+dwjz_" + code + r"\s*=\s*(\[.*\])\s*;?\s*", _text(body), re.S)
    require(match is not None, "SOURCE_FORMAT_CHANGED", "备用正式净值格式或代码不符")
    try:
        rows = json.loads(match[1], parse_float=Decimal)
        require(isinstance(rows, list) and 20 <= len(rows) <= 20000, "SOURCE_FORMAT_CHANGED", "备用净值数量异常")
        records, previous = [], ""
        for row in rows:
            require(isinstance(row, list) and len(row) == 2, "SOURCE_FORMAT_CHANGED", "备用净值结构异常")
            date_ = datetime.strptime(row[0], "%Y%m%d").date().isoformat()
            require(previous < date_ <= today, "SOURCE_DATE_INVALID", "备用净值重复、倒序或未来日期")
            value = dec(row[1], positive=True)
            if date_ >= since:
                records.append({"effective_date": date_, "value": value, "corporate_action": ""})
            previous = date_
        require(bool(records), "SOURCE_EMPTY", "备用来源没有所需区间净值")
    except (ValueError, TypeError, RecursionError):
        raise Problem("SOURCE_FORMAT_CHANGED", "备用净值字段无效")
    return {"provider_id": "THS_PUBLIC_NAV", "fund_code": code, "name": SUPPORTED[code],
            "source": "同花顺披露日净值", "source_url": url, "records": records,
            "corporate_actions_complete": False,
            "raw_sha256": hashlib.sha256(body).hexdigest(), "raw_body": body}


def fetch_official_nav(code, today, *, client=None):
    try:
        return dict(fetch_public_nav(code, today, client=client), provider_id="EASTMONEY_PUBLIC_NAV")
    except Problem as first:
        if first.code in {"RATE_LIMITED", "SOURCE_NOT_SUPPORTED"}:
            raise
        return dict(fetch_ths_nav(code, today, client=client),
                    fallback_errors=[{"provider": "EASTMONEY_PUBLIC_NAV", "code": first.code}])
