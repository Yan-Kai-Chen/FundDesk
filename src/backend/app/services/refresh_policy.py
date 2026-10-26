"""Automatic pacing is independent of an explicit user refresh."""
from datetime import timedelta

from app.domain.finance import require, stamp

AUTO_REFRESH_SECONDS = 600
COLLECTION_LEASE_SECONDS = 30


def require_refresh_slot(previous, now, *, manual=False):
    if not previous:
        return
    attempted = stamp(previous["attempted_at"]) if previous.get("attempted_at") else None
    current = stamp(now)
    require(not (previous.get("status") == "CHECKING" and attempted and
                 current - attempted < timedelta(seconds=COLLECTION_LEASE_SECONDS)),
            "COLLECTION_IN_PROGRESS", "当前正在刷新，完成后可再次刷新")
    if not manual:
        require((not attempted or current - attempted >= timedelta(seconds=AUTO_REFRESH_SECONDS)) and
                (not previous.get("next_attempt_at") or stamp(previous["next_attempt_at"]) <= current),
                "RATE_LIMITED", "自动采集每 10 分钟一次；可点击刷新立即检查")
