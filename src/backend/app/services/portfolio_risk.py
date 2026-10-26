"""One read-only projection: no network, fitting, snapshots or ledger writes."""
from app.domain.portfolio_risk import portfolio_risk


class PortfolioRiskMixin:
    def portfolio_risk(self, snapshot=None):
        with self.store.connect() as c:
            c.execute("BEGIN")
            snapshot = snapshot if snapshot is not None else self.snapshot(c)
            datasets = self.research_datasets(c)
            return portfolio_risk(snapshot, self.daily_t_references(snapshot), datasets)
