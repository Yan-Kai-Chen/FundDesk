"""Verified reference sessions for research; not a fund subscription calendar."""
from datetime import date, timedelta
from app.domain.market_calendar import CN_HOLIDAYS

SOURCES = [
    "https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml",
    "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml",
]
HOLIDAYS_2025 = {"2025-01-01"}
for a, b in [("2025-01-28", "2025-02-04"), ("2025-04-04", "2025-04-06"),
             ("2025-05-01", "2025-05-05"), ("2025-05-31", "2025-06-02"), ("2025-10-01", "2025-10-08")]:
    start, end = date.fromisoformat(a), date.fromisoformat(b)
    HOLIDAYS_2025.update((start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1))


def reference_sessions():
    start, end = date(2025, 1, 1), date(2026, 12, 31)
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5 and
            (start + timedelta(days=i)).isoformat() not in HOLIDAYS_2025 | CN_HOLIDAYS]
