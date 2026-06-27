import pytest

from agent.access_db import InMemoryAccessDatabase


def test_upsert_inserts_and_updates():
    db = InMemoryAccessDatabase({"unit_master": [{"unit_code": "A-1", "status": "AVAILABLE"}]})
    written = db.upsert_rows(
        "unit_master",
        "unit_code",
        [
            {"unit_code": "A-1", "status": "BOOKED"},  # update
            {"unit_code": "A-2", "status": "AVAILABLE"},  # insert
        ],
    )
    assert written == 2
    rows = {r["unit_code"]: r for r in db.tables["unit_master"]}
    assert rows["A-1"]["status"] == "BOOKED"
    assert rows["A-2"]["status"] == "AVAILABLE"


def test_read_rows_projects_columns():
    db = InMemoryAccessDatabase(
        {"t": [{"k": "1", "a": "x", "secret": "nope"}]}
    )
    rows = db.read_rows("t", ["k", "a"])
    assert rows == [{"k": "1", "a": "x"}]


def test_transaction_rolls_back_on_error():
    db = InMemoryAccessDatabase({"t": [{"k": "1", "v": "orig"}]})
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.upsert_rows("t", "k", [{"k": "1", "v": "changed"}])
            raise RuntimeError("boom")
    assert db.tables["t"][0]["v"] == "orig"
    assert db.rolled_back == 1
    assert db.committed == 0


def test_transaction_commits_on_success():
    db = InMemoryAccessDatabase({"t": [{"k": "1", "v": "orig"}]})
    with db.transaction():
        db.upsert_rows("t", "k", [{"k": "1", "v": "changed"}])
    assert db.tables["t"][0]["v"] == "changed"
    assert db.committed == 1
