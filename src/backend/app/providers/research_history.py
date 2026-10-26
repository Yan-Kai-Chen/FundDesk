"""Bounded public history for research, never a ledger/NAV writer."""
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import hashlib
import re

import httpx

from app.domain.finance import Problem, SHANGHAI, dec, require, stamp
from app.providers.public_nav import SUPPORTED, literal
from app.research_methods.engine.total_return import reconstruct


CASH_CODE = "000198"
CASH_NAME = "天弘余额宝货币"
CASH_IDENTITY_SOURCE = "https://cdn-thweb.tianhongjijin.com.cn/fundnotice/02eae2effe192cc2b98dc83f76e0ac8b.pdf"
PRODUCTS = {
    "012183": ("ACTIVE_EQUITY", "CN_HK", "AI_APPLICATIONS"),
    "021528": ("ACTIVE_EQUITY", "CN_DOMESTIC", "ACTIVE_GROWTH"),
    "012738": ("PASSIVE_EQUITY", "CN_DOMESTIC", "INNOVATIVE_DRUG"),
    "019633": ("PASSIVE_EQUITY", "CN_DOMESTIC", "SEMICONDUCTOR_EQUIPMENT"),
    "017954": ("ENHANCED_EQUITY", "CN_HK", "CSI1000_ENHANCED"),
    "008987": ("PASSIVE_GOLD", "CN_DOMESTIC", "SHAU_GOLD"),
}


def fetch_body(code, client=None):
    require(code in SUPPORTED or code == CASH_CODE, "SOURCE_NOT_SUPPORTED", "研究历史来源尚未核实")
    owned = client is None
    client = client or httpx.Client(timeout=8, follow_redirects=False)
    try:
        with client.stream("GET", f"https://fund.eastmoney.com/pingzhongdata/{code}.js", timeout=8, follow_redirects=False) as r:
            require(r.status_code == 200, "SOURCE_UNAVAILABLE", "研究历史来源暂不可用，保留已有数据")
            chunks, size = [], 0
            for chunk in r.iter_bytes(chunk_size=65536):
                size += len(chunk)
                require(size <= 2_000_000, "SOURCE_TOO_LARGE", "研究历史响应超过2MB")
                chunks.append(chunk)
            return b"".join(chunks)
    except (httpx.HTTPError, OSError):
        raise Problem("SOURCE_UNAVAILABLE", "研究历史连接失败，保留已有数据")
    finally:
        if owned:
            client.close()


def _identity(body, code):
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError:
        raise Problem("SOURCE_FORMAT_CHANGED", "研究历史编码无效")
    require(literal(text, "fS_code") == code and literal(text, "fS_name") == (CASH_NAME if code == CASH_CODE else SUPPORTED[code]),
            "SOURCE_IDENTITY_MISMATCH", "研究来源代码或份额类别不符")
    return text


def _date(ms):
    require(type(ms) is int, "SOURCE_FORMAT_CHANGED", "历史日期格式异常")
    try:
        return datetime.fromtimestamp(ms / 1000, SHANGHAI).date().isoformat()
    except (ValueError, OverflowError, OSError):
        raise Problem("SOURCE_FORMAT_CHANGED", "历史日期无效")


def parse_history(body, code, observed_at):
    text, now = _identity(body, code), stamp(observed_at).astimezone(SHANGHAI)
    rows = literal(text, "Data_netWorthTrend")
    require(isinstance(rows, list) and 20 <= len(rows) <= 20000, "SOURCE_FORMAT_CHANGED", "历史净值数量异常")
    nav, dividends, splits, dates, actions = [], [], [], [], []
    prior = ""
    for row in rows:
        require(isinstance(row, dict), "SOURCE_FORMAT_CHANGED", "历史净值结构异常")
        date = _date(row.get("x"))
        require(date > prior and date <= now.date().isoformat(), "SOURCE_DATE_INVALID", "历史日期重复、倒序或来自未来")
        prior = date
        # A same-day placeholder before close is not a completed observation.
        if date == now.date().isoformat() and now.hour < 15:
            continue
        value = dec(row.get("y"), positive=True)
        event = str(row.get("unitMoney") or "").strip()
        dividend, split = "0", "1"
        if event:
            match = re.fullmatch(r"分红[：:]\s*每(10)?份(?:基金份额)?派(?:发)?现金\s*([0-9.]+)元", event)
            require(match is not None, "CORPORATE_ACTION_REVIEW", "存在尚未适配的分红/拆分说明，研究历史暂停更新")
            dividend = str(dec(match[2]) / (10 if match[1] else 1))
            actions.append({"date": date, "text": event, "cash_per_old_unit": dividend})
        nav.append(str(value)); dividends.append(dividend); splits.append(split); dates.append(date)
    require(dividends[0] == "0", "CORPORATE_ACTION_REVIEW", "首个净值带分红，缺少前一净值")
    levels = reconstruct(nav, dividends, splits)
    # A compact current series, not a fictitious historical publication archive.
    return {"fund_code": code, "series_kind": "FUND_TOTAL_RETURN", "basis": "RECONSTRUCTED_PUBLIC_NAV_AND_ACTIONS",
            "rows": [[d, n, format(level, ".16f")] for d, n, level in zip(dates, nav, levels)],
            "actions": actions, "corporate_actions_verified": True, "observed_at": observed_at,
            "source": "天天基金公开披露净值与分红说明", "source_url": f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
            "raw_sha256": hashlib.sha256(body).hexdigest(), "publication_clock": "FIRST_OBSERVED_CONSERVATIVE",
            "notice": "历史重建可用于当前以后研究，不能据此声称过去时点样本外表现。"}


def parse_cash(body, observed_at):
    text, today = _identity(body, CASH_CODE), stamp(observed_at).astimezone(SHANGHAI).date()
    retained_from = today.replace(year=today.year - 2, month=1, day=1)
    rows = literal(text, "Data_millionCopiesIncome")
    require(isinstance(rows, list) and 300 <= len(rows) <= 20000, "SOURCE_FORMAT_CHANGED", "现金参照历史不足")
    output, prior, level = [], None, Decimal("100")
    with localcontext() as context:
        context.prec = 36
        for row in rows:
            require(isinstance(row, list) and len(row) == 2, "SOURCE_FORMAT_CHANGED", "万份收益结构无效")
            date = datetime.fromisoformat(_date(row[0])).date()
            # Retain three calendar years so a January month-end anchor also
            # has 252 prior sessions. Earlier inception gaps must not
            # be filled or invalidate a later complete research window.
            if date < retained_from:
                continue
            require(date <= today and (prior is None or date == prior + timedelta(days=1)),
                    "SOURCE_DATE_INVALID", "现金参照缺少连续日收益，不能前填")
            # Every daily distribution, including weekends, compounds before session sampling.
            income = dec(row[1], nonnegative=False)
            require(abs(income) < 100, "SOURCE_FORMAT_CHANGED", "万份收益超出合理输入范围")
            level *= 1 + income / 10000
            output.append([date.isoformat(), format(level, ".16f")]); prior = date
    require(len(output) >= 300, "SOURCE_EMPTY", "保留窗口内的现金参照不足300个日收益")
    return {"fund_code": CASH_CODE, "series_kind": "CNY_CASH_TOTAL_RETURN", "currency": "CNY", "rows": output, "retained_from": retained_from.isoformat(),
            "observed_at": observed_at, "source": "天弘余额宝货币万份收益再投资现金参照", "source_url": f"https://fund.eastmoney.com/pingzhongdata/{CASH_CODE}.js",
            "identity_source": CASH_IDENTITY_SOURCE, "raw_sha256": hashlib.sha256(body).hexdigest(),
            "notice": "货币基金研究参照，并非银行存款或无风险利率；不代表你的余额宝持仓。"}
