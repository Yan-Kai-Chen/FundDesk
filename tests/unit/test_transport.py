from decimal import Decimal
import httpx
import pytest
from app.providers.transport import JsonTransport
from app.domain.finance import Problem


def transport(handler, **kw):
    return JsonTransport({'example.test'},client=httpx.Client(transport=httpx.MockTransport(handler)),sleeper=lambda _:None,**kw)


def test_json_decimal_and_rate_limit():
    t=transport(lambda r:httpx.Response(200,content=b'{"value":1.2345}',headers={'content-type':'application/json'}))
    assert t.fetch('https://example.test/nav')['value']==Decimal('1.2345')
    with pytest.raises(Problem,match='刷新过快'):t.fetch('https://example.test/nav')


@pytest.mark.parametrize('url',['http://example.test/nav','https://other.test/nav','https://' + 'user:secret' + '@example.test/nav'])
def test_host_and_scheme_allowlist(url):
    with pytest.raises(Problem):transport(lambda r:httpx.Response(200)).fetch(url)


@pytest.mark.parametrize('response',[httpx.Response(302,headers={'location':'https://other.test'}),httpx.Response(200,content=b'callback({})',headers={'content-type':'application/javascript'}),httpx.Response(200,content=b'bad',headers={'content-type':'application/json'}),httpx.Response(429)])
def test_redirect_jsonp_invalid_json_and_rate_limit_are_explicit(response):
    with pytest.raises(Problem):transport(lambda r:response).fetch('https://example.test/nav')


def test_size_cap_and_two_attempts():
    t=transport(lambda r:httpx.Response(200,content=b'{"value":"too large"}',headers={'content-type':'application/json'}),max_bytes=5)
    with pytest.raises(Problem,match='大小'):t.fetch('https://example.test/nav')
    calls=[]
    def timeout(request):calls.append(1);raise httpx.ReadTimeout('synthetic timeout',request=request)
    with pytest.raises(Problem,match='有限重试'):transport(timeout).fetch('https://example.test/nav')
    assert len(calls)==2
