from datetime import date, timedelta
from decimal import Decimal as D
import json

import httpx
import pytest

from app.domain.finance import Problem
from app.providers.public_market import (parse_ths_estimate, parse_fundgz, fetch_estimate,
                                         fetch_official_nav, fetch_ths_nav)

NOW = "2026-10-20T05:30:00Z"


def ths_body(points="1328,1.22,1.2,0.000;1329,1.23,1.2,0.000", date_="2026-10-20", base="1.2", ref="2026-10-19"):
    return f"vm_fd_012183='{ref};0930-1130,1300-1500|{date_}~{base}~{points}'".encode()


def fundgz_body(**kwargs):
    x = {"fundcode": "012183", "name": "广发沪港深精选混合C", "jzrq": "2026-10-19", "dwjz": "1.2",
         "gsz": "1.23", "gztime": "2026-10-20 13:29"}
    return ("jsonpgz(" + json.dumps(dict(x, **kwargs), ensure_ascii=False) + ");").encode()


def nav_body():
    rows = [[(date(2026, 9, 20) + timedelta(days=i)).strftime("%Y%m%d"), "1.2"] for i in range(30)]
    return ("var dwjz_012183=" + json.dumps(rows)).encode()


def test_ths_literal_decimal_timezone_and_minute_history():
    r = parse_ths_estimate(ths_body(), "012183", NOW)
    assert r["reference_date"] == "2026-10-19" and r["reference_nav"] == D("1.2")
    assert r["records"][-1]["value"] == D("1.23")
    assert r["records"][-1]["as_of"] == "2026-10-20T13:29:00+08:00"
    assert len(r["raw_sha256"]) == 64 and r["provider_id"] == "THS_PUBLIC_ESTIMATE"


@pytest.mark.parametrize("body", [
    ths_body().replace(b'012183', b'012182'), ths_body() + b';fetch("https://example.invalid")',
    b'<html>page not found</html>', ths_body(date_="2026-10-19"), ths_body(date_="2026-10-21"),
    ths_body(base="0"), ths_body(ref="2026-10-20"),
    ths_body(points="1329,NaN,1.2,0"), ths_body(points="1329,1.2,1.3,0"),
    ths_body(points="1329,1.2,1.2,0;1329,1.2,1.2,0"),
    ths_body(points="1329,1.2,1.2,0;1328,1.2,1.2,0"),
    ths_body(points="1260,1.2,1.2,0"), ths_body(points="1230,1.2,1.2,0"),
    ths_body(points="1331,1.2,1.2,0"), ths_body(points="1314,1.2,1.2,0"),
    ths_body(points="1329,2.5,1.2,0"), ths_body(points=""), b'x' * 131073, b'\xff',
])
def test_ths_untrusted_identity_time_syntax_and_outliers_are_blocked(body):
    with pytest.raises(Problem):
        parse_ths_estimate(body, "012183", NOW)


def test_fundgz_strict_jsonp_identity_and_no_script_execution():
    assert parse_fundgz(fundgz_body(), "012183", NOW)["records"][0]["value"] == D("1.23")
    for body in [fundgz_body(fundcode="012182"), fundgz_body(name="另一份额"),
                 fundgz_body(gztime="2026-10-19 13:29"), fundgz_body() + b'evil()', b'jsonpgz({bad});']:
        with pytest.raises(Problem):
            parse_fundgz(body, "012183", NOW)


def test_live_primary_then_fallback_without_installing_or_executing_scripts():
    calls = []
    def primary(r):
        calls.append(r.url.host)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=ths_body())
    with httpx.Client(transport=httpx.MockTransport(primary)) as c:
        r = fetch_estimate("012183", lambda: NOW, client=c)
        assert len(calls) == 1 and not r["fallback_checked"]
    calls.clear()
    def fallback(r):
        calls.append(r.url.host)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, content=fundgz_body())
    with httpx.Client(transport=httpx.MockTransport(fallback)) as c:
        r = fetch_estimate("012183", NOW, client=c)
        assert r["provider_id"] == "EASTMONEY_PUBLIC_ESTIMATE" and r["fallback_checked"]
        assert calls == ["gz-fund.10jqka.com.cn", "fundgz.1234567.com.cn"]


def test_primary_failure_fallback_cooldown_and_upstream_rate_limit():
    for status, allowed, expected in [(429, True, 1), (503, False, 1), (503, True, 2), (302, False, 1)]:
        calls = []
        def bad(r):
            calls.append(r.url)
            return httpx.Response(status, content=b'<html>missing</html>')
        with httpx.Client(transport=httpx.MockTransport(bad)) as c, pytest.raises(Problem):
            fetch_estimate("012183", NOW, client=c, allow_fallback=allowed)
        assert len(calls) == expected


def test_bounded_download_and_network_errors():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'x' * 131073))) as c:
        with pytest.raises(Problem):
            fetch_estimate("012183", NOW, client=c, allow_fallback=False)
    def timeout(r):
        raise httpx.ReadTimeout("synthetic", request=r)
    with httpx.Client(transport=httpx.MockTransport(timeout)) as c, pytest.raises(Problem):
        fetch_estimate("012183", NOW, client=c, allow_fallback=False)
    with pytest.raises(Problem):
        fetch_estimate("not-a-code", NOW)


def test_official_nav_fallback_is_explicit_about_missing_corporate_action_metadata():
    def handler(r):
        if r.url.host == "fund.eastmoney.com":
            return httpx.Response(503)
        return httpx.Response(200, content=nav_body())
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        r = fetch_official_nav("012183", "2026-10-20", client=c)
    assert r["provider_id"] == "THS_PUBLIC_NAV" and len(r["records"]) == 30
    assert r["corporate_actions_complete"] is False and r["fallback_errors"]


@pytest.mark.parametrize("body", [b'<html>missing</html>', nav_body().replace(b'012183', b'012182'),
                                 nav_body() + b';evil()', b'var dwjz_012183=[]',
                                 b'var dwjz_012183=' + json.dumps([["20261021", "1.2"]] * 20).encode()] )
def test_nav_fallback_rejects_html_expressions_identity_and_future(body):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))) as c:
        with pytest.raises(Problem):
            fetch_ths_nav("012183", "2026-10-20", client=c)
