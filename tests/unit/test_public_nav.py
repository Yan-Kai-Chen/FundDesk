from datetime import datetime, timedelta
from decimal import Decimal
import json
import httpx
import pytest

from app.domain.finance import SHANGHAI, Problem
from app.providers.public_nav import parse_public_nav, fetch_public_nav


def body(code="012183", expression=None, count=30, name="广发沪港深精选混合C"):
    records = [{"x":int((datetime(2026,8,1,tzinfo=SHANGHAI)+timedelta(days=i)).timestamp()*1000),
                "y":1.1234,"unitMoney":""} for i in range(count)]
    return (f'var fS_name = "{name}"; var fS_code = "{code}";'
            'var Data_netWorthTrend = ' + (expression or json.dumps(records)) + ';').encode()


def test_public_parser_keeps_decimal_and_local_date_without_running_script():
    result = parse_public_nav(body(),"012183","2026-10-20")
    assert result["records"][0]["effective_date"] == "2026-08-01"
    assert result["records"][0]["value"] == Decimal("1.1234")
    assert len(result["raw_sha256"]) == 64


def test_unused_old_chart_float_artifact_does_not_block_current_navs():
    old = '{"x":1745337600000,"y":1.3376999999999999,"unitMoney":""},'
    data = body().decode().replace('var Data_netWorthTrend = [', 'var Data_netWorthTrend = ['+old).encode()
    assert len(parse_public_nav(data, '012183', '2026-10-23')['records']) == 30
    with pytest.raises(Problem):
        parse_public_nav(data, '012183', '2026-10-23', since='2025-01-01')


def test_second_verified_fund_requires_its_own_code_and_share_class_name():
    data = body(code="021528", name="财通成长优选混合C")
    assert parse_public_nav(data, "021528", "2026-10-20")["fund_code"] == "021528"
    for invalid in [body(code="021528"), body(code="021528", name="财通成长优选混合A"), data.replace(b'021528', b'001480')]:
        with pytest.raises(Problem) as exc:
            parse_public_nav(invalid, "021528", "2026-10-20")
        assert exc.value.code == "SOURCE_IDENTITY_MISMATCH"


@pytest.mark.parametrize('code,name', [('012738','广发创新药ETF联接C'),
    ('019633','国泰半导体设备ETF联接C'), ('017954','汇添富中证1000指数增强C'),
    ('006810','泰康香港银行指数C')])
def test_added_funds_require_their_own_verified_identity(code, name):
    assert parse_public_nav(body(code=code, name=name), code, '2026-10-21')['fund_code'] == code
    for payload in [body(code=code, name=name[:-1]+'A'), body(code='012183', name=name)]:
        with pytest.raises(Problem) as exc: parse_public_nav(payload, code, '2026-10-21')
        assert exc.value.code == 'SOURCE_IDENTITY_MISMATCH'


@pytest.mark.parametrize("payload", [body(code="012182"),body(expression='fetch("https://invalid.example")'),
    body(expression='[{"x":1,"y":NaN}]'), body(count=2), body()+b'var fS_code="012183";'])
def test_identity_changes_and_non_json_expressions_are_rejected(payload):
    with pytest.raises(Problem): parse_public_nav(payload,"012183","2026-10-20")


def test_bad_date_order_and_future_values_rejected():
    with pytest.raises(Problem): parse_public_nav(body(),"012183","2026-08-05")
    with pytest.raises(Problem): parse_public_nav(body(),"012182","2026-10-20")
    data=body().decode().replace('"x": 1785513600000','"x": 999999999999999999999')
    with pytest.raises(Problem): parse_public_nav(data.encode(),"012183","2026-10-20")


def test_public_fetch_redirect_limit_error_and_strict_format():
    for status,content_type,payload in [(302,'text/plain',b''),(429,'text/plain',b''),(200,'text/html',b'<html>blocked</html>'),
                                         (200,'application/javascript',b'x'*2_000_001)]:
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(status,headers={'content-type':content_type},content=payload))) as client:
            with pytest.raises(Problem): fetch_public_nav('012183','2026-10-20',client=client)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'application/javascript'},content=body()))) as client:
        assert len(fetch_public_nav('012183','2026-10-20',client=client)['records']) == 30
