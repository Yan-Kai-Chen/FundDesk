"""A read-only projection of permanent forecast documents, with no new storage."""
from decimal import Decimal

from app.domain.forecast_scorecard import build_forecast_scorecard
from app.domain.forecasting import ENGINE_VERSION
from app.persistence.store import Store
from app.providers.research_history import PRODUCTS


def _strings(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: _strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strings(v) for v in value]
    return value


class ForecastScorecardMixin:
    def forecast_scorecard(self):
        # One consistent read transaction; GET does not resolve or fit forecasts.
        with self.store.connect() as c:
            c.execute("BEGIN")
            result = build_forecast_scorecard(Store.docs(c, "fund"), Store.docs(c, "forecast_prediction"),
                Store.docs(c, "forecast_resolution"), now=self.now(), engine_version=ENGINE_VERSION,
                supported_codes=PRODUCTS)
            result["mode"] = self.mode
            return _strings(result)
