"""Bounded JSON-only transport for future approved providers; not enabled by the app."""
from decimal import Decimal
from threading import Lock
import json
import time
from urllib.parse import urlsplit
import httpx
from app.domain.finance import Problem, require


class JsonTransport:
    def __init__(self, allowed_hosts, *, client=None, min_interval=1.0, max_bytes=2_000_000, attempts=2,
                 clock=time.monotonic, sleeper=time.sleep):
        self.allowed_hosts = set(allowed_hosts)
        self.client = client or httpx.Client(timeout=5.0, follow_redirects=False, limits=httpx.Limits(max_connections=2))
        self.min_interval, self.max_bytes, self.attempts = min_interval, max_bytes, min(attempts, 2)
        self.clock, self.sleeper, self.next_at, self.lock = clock, sleeper, 0, Lock()

    def fetch(self, url):
        parsed = urlsplit(url)
        require(parsed.scheme == "https" and parsed.hostname in self.allowed_hosts and not parsed.username and not parsed.password,
                "SOURCE_NOT_ALLOWED", "数据源须为明确允许的 HTTPS 主机")
        with self.lock:
            require(self.clock() >= self.next_at, "RATE_LIMITED", "数据源刷新过快，请稍后重试")
            self.next_at = self.clock() + self.min_interval
            for attempt in range(self.attempts):
                try:
                    with self.client.stream("GET", url, timeout=5.0, follow_redirects=False) as r:
                        if r.status_code == 429:
                            raise Problem("RATE_LIMITED", "上游限流，本轮停止请求")
                        if r.status_code >= 500:
                            if attempt + 1 < self.attempts:
                                self.sleeper(.1 * (2 ** attempt))
                                continue
                            raise Problem("SOURCE_UNAVAILABLE", "上游服务暂不可用")
                        require(r.status_code == 200, "SOURCE_HTTP_ERROR", "数据源返回非成功状态，不跟随重定向")
                        require("json" in r.headers.get("content-type", "").lower(), "SOURCE_FORMAT_CHANGED", "数据源不是 JSON；不会执行 JavaScript 或 JSONP")
                        chunks, size = [], 0
                        for chunk in r.iter_bytes(chunk_size=65536):
                            size += len(chunk)
                            require(size <= self.max_bytes, "SOURCE_TOO_LARGE", "数据源响应超过允许大小")
                            chunks.append(chunk)
                        try:
                            result = json.loads(b"".join(chunks), parse_float=Decimal)
                        except (ValueError, UnicodeError):
                            raise Problem("SOURCE_FORMAT_CHANGED", "数据源 JSON 格式无效")
                        require(isinstance(result, (dict, list)), "SOURCE_FORMAT_CHANGED", "数据源响应结构改变")
                        return result
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt + 1 >= self.attempts:
                        raise Problem("SOURCE_UNAVAILABLE", "数据源超时或网络故障，已停止有限重试")
                    self.sleeper(.1 * (2 ** attempt))
        raise Problem("SOURCE_UNAVAILABLE", "数据源不可用")

    def close(self):
        self.client.close()
