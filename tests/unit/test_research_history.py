from datetime import date, datetime, timedelta
from decimal import Decimal as D
import json

import pytest

from app.domain.finance import Problem, SHANGHAI
from app.providers.research_history import parse_history, parse_cash


def timestamp(day):
    return int(datetime.combine(day, datetime.min.time(), SHANGHAI).timestamp() * 1000)


def body(code, name, field, rows):
    return f'var fS_code={json.dumps(code)}; var fS_name={json.dumps(name)}; var {field}={json.dumps(rows)};'.encode()


def history_body(event=None):
    start = date(2026, 1, 1)
    rows = [{"x": timestamp(start + timedelta(days=i)), "y": 1, "unitMoney": ""} for i in range(20)]
    if event:
        rows[1].update(y=.9, unitMoney=event)
        rows[2].update(y=.99)
    return body("008987", "广发上海金ETF联接C", "Data_netWorthTrend", rows)


def test_total_return_reinvests_dividend_and_preserves_first_observed_boundary():
    result = parse_history(history_body("分红：每份派现金0.10元"), "008987", "2026-10-20T00:00:00Z")
    assert D(result["rows"][0][-1]) == 100
    assert D(result["rows"][1][-1]) == 100
    assert D(result["rows"][2][-1]) == 110
    assert result["observed_at"] == "2026-10-20T00:00:00Z"
    assert result["publication_clock"] == "FIRST_OBSERVED_CONSERVATIVE"


def test_unknown_actions_wrong_identity_and_nonliteral_code_are_rejected():
    for payload in [history_body("拆分：份额变化待核对"), history_body().replace(b'008987', b'008986'), history_body().replace(b'var Data_netWorthTrend=', b'var Data_netWorthTrend=execute(')]:
        with pytest.raises(Problem): parse_history(payload, "008987", "2026-10-20T00:00:00Z")


def test_cash_compounds_every_calendar_day_and_does_not_fill_missing_days():
    start = date(2025, 1, 1)
    rows = [[timestamp(start + timedelta(days=i)), .2] for i in range(300)]
    result = parse_cash(body("000198", "天弘余额宝货币", "Data_millionCopiesIncome", rows), "2026-10-20T00:00:00Z")
    assert abs(D(result["rows"][-1][1]) / D(result["rows"][-2][1]) - D("1.00002")) < D("1e-16")
    rows[150][0] += 86400000
    with pytest.raises(Problem): parse_cash(body("000198", "天弘余额宝货币", "Data_millionCopiesIncome", rows), "2026-10-20T00:00:00Z")
