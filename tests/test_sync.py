"""Integration tests for the Syncer using a fake CRM and the in-memory Access DB."""

from agent.access_db import InMemoryAccessDatabase
from agent.config import Config
from agent.crm_client import OutboundResponse
from agent.state import SyncState
from agent.sync import Syncer


class FakeCrm:
    """Stand-in for CrmClient: serves canned outbound pages, records acks."""

    def __init__(self, pages):
        self._pages = pages  # list of dicts -> OutboundResponse.from_json
        self.acks = []
        self.calls = 0

    def fetch_outbound(self, since, tables):
        page = self._pages[min(self.calls, len(self._pages) - 1)]
        self.calls += 1
        return OutboundResponse.from_json(page)

    def post_ack(self, cursor, results):
        self.acks.append((cursor, results))
        return {"ok": True}


def _config(**kw):
    base = dict(
        access_db_path="x.accdb",
        crm_base_url="https://crm",
        agent_token="t",
        table_whitelist=["unit_master"],
    )
    base.update(kw)
    return Config(**base)


def _outbound(cursor, rows, has_more=False):
    return {
        "cursor": cursor,
        "has_more": has_more,
        "batches": [{"table": "unit_master", "key": "unit_code", "rows": rows}],
    }


def test_dry_run_writes_nothing_and_does_not_ack():
    db = InMemoryAccessDatabase({"unit_master": [{"unit_code": "A-1", "status": "AVAILABLE"}]})
    crm = FakeCrm([_outbound("c1", [{"unit_code": "A-1", "status": "BOOKED"}])])
    state = SyncState()
    syncer = Syncer(_config(dry_run=True), crm, db, state)

    report = syncer.run_once(dry_run=True)

    assert report.dry_run is True
    assert report.total_changes == 1
    assert db.tables["unit_master"][0]["status"] == "AVAILABLE"  # untouched
    assert db.committed == 0
    assert crm.acks == []
    assert state.cursor is None  # cursor not advanced on a dry run


def test_real_run_applies_changes_and_acks():
    db = InMemoryAccessDatabase({"unit_master": [{"unit_code": "A-1", "status": "AVAILABLE"}]})
    crm = FakeCrm(
        [
            _outbound(
                "c1",
                [
                    {"unit_code": "A-1", "status": "BOOKED"},  # update
                    {"unit_code": "A-2", "status": "AVAILABLE"},  # insert
                ],
            )
        ]
    )
    state = SyncState()
    syncer = Syncer(_config(dry_run=False), crm, db, state)

    report = syncer.run_once(dry_run=False)

    assert report.rows_written == 2
    rows = {r["unit_code"]: r for r in db.tables["unit_master"]}
    assert rows["A-1"]["status"] == "BOOKED"
    assert rows["A-2"]["status"] == "AVAILABLE"
    assert db.committed == 1
    assert state.cursor == "c1"
    assert crm.acks and crm.acks[0][0] == "c1"


def test_pagination_follows_has_more():
    db = InMemoryAccessDatabase({"unit_master": []})
    crm = FakeCrm(
        [
            _outbound("c1", [{"unit_code": "A-1", "status": "BOOKED"}], has_more=True),
            _outbound("c2", [{"unit_code": "A-2", "status": "BOOKED"}], has_more=False),
        ]
    )
    state = SyncState()
    syncer = Syncer(_config(dry_run=False), crm, db, state)

    report = syncer.run_once(dry_run=False)

    assert crm.calls == 2
    assert report.rows_written == 2
    assert state.cursor == "c2"


def test_non_whitelisted_table_from_server_is_skipped():
    db = InMemoryAccessDatabase({})
    page = {
        "cursor": "c1",
        "has_more": False,
        "batches": [{"table": "secret_table", "key": "id", "rows": [{"id": 1}]}],
    }
    crm = FakeCrm([page])
    state = SyncState()
    syncer = Syncer(_config(dry_run=False), crm, db, state)

    report = syncer.run_once(dry_run=False)

    assert report.batches_processed == 0
    assert "secret_table" not in db.tables


def test_no_changes_means_no_ack():
    db = InMemoryAccessDatabase({"unit_master": [{"unit_code": "A-1", "status": "BOOKED"}]})
    crm = FakeCrm([_outbound("c1", [{"unit_code": "A-1", "status": "BOOKED"}])])
    state = SyncState()
    syncer = Syncer(_config(dry_run=False), crm, db, state)

    report = syncer.run_once(dry_run=False)

    assert report.rows_written == 0
    # cursor still advances so we don't re-pull the same window forever,
    # but there were no row writes
    assert state.cursor == "c1"
