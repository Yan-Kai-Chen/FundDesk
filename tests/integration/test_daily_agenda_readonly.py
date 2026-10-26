from copy import deepcopy

from app.domain.daily_agenda import daily_agenda
from conftest import opening


def test_agenda_uses_real_snapshot_references_without_database_or_ledger_writes(wb):
    opening(wb)
    snapshot = wb.snapshot()
    references = wb.daily_t_references(snapshot)
    before = deepcopy((snapshot, references))
    with wb.store.connect() as c:
        documents = [tuple(row) for row in c.execute("SELECT * FROM documents ORDER BY kind,id")]
        events = [tuple(row) for row in c.execute("SELECT * FROM events ORDER BY seq")]
    first = daily_agenda(snapshot, references)
    assert daily_agenda(snapshot, references) == first
    assert first["revision"] == snapshot["revision"] and len(first["funds"]) == 1
    assert (snapshot, references) == before
    with wb.store.connect() as c:
        assert [tuple(row) for row in c.execute("SELECT * FROM documents ORDER BY kind,id")] == documents
        assert [tuple(row) for row in c.execute("SELECT * FROM events ORDER BY seq")] == events
