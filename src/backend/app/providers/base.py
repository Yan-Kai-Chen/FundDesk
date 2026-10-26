"""Provider capability contract; public NAV access is scoped to verified codes."""
from dataclasses import dataclass, asdict
from typing import Protocol
from app.domain.finance import Problem
from app.providers.public_nav import SUPPORTED, EQUITY_ESTIMATE_CODES


@dataclass(frozen=True)
class Capabilities:
    id: str
    mode: str
    enabled: bool
    supported_value_kinds: tuple[str, ...]
    latency: str
    access_basis: str
    timeout_seconds: int = 5
    max_response_bytes: int = 2_000_000
    max_attempts: int = 2


class Provider(Protocol):
    def capabilities(self) -> Capabilities: ...
    def healthcheck(self) -> dict: ...


def capabilities():
    return [asdict(Capabilities("DemoProvider", "demo", True, ("OFFICIAL_NAV", "THIRD_PARTY_ESTIMATE"), "synthetic fixed clock", "synthetic fixtures")),
            asdict(Capabilities("ManualCsvProvider", "real", True, ("OFFICIAL_NAV", "THIRD_PARTY_ESTIMATE", "LOCAL_ESTIMATE", "MANUAL_VALUE"), "user supplied timestamps", "user supplied local records")),
            asdict(Capabilities("PublicNavProvider", "real", True, ("OFFICIAL_NAV",), "automatic daily checks; every 10 minutes after 16:00 including same-day revisions",
                                ' / '.join(code+' C' for code in sorted(SUPPORTED))+"; public Eastmoney with THS fallback; literal data only")),
            asdict(Capabilities("PublicEstimateProvider", "real", True, ("THIRD_PARTY_ESTIMATE",), "every 10 minutes live and after close until official NAV; manual refresh bypasses interval",
                                ' / '.join(code+' C' for code in sorted(EQUITY_ESTIMATE_CODES))+"; public THS source; Eastmoney fallback; estimates, not official NAV")),
            asdict(Capabilities("GoldReferenceProvider", "real", True, ("LOCAL_ESTIMATE",), "every 10 minutes; SGE day quotes then published SHAU PM fixing",
                                "008987 C only; SGE public delayed CNY/gram data; 95% benchmark approximation; not official NAV")),
            asdict(Capabilities("NetworkProvider", "real", False, (), "not tested", "other instruments and sources not established"))]


def network_fetch_disabled():
    raise Problem("NOT_SUPPORTED", "真实联网数据源尚未启用，请使用手工或 CSV 读取路径")
