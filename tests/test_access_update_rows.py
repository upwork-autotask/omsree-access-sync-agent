"""InMemoryAccessDatabase.update_rows: UPDATE-only, never inserts."""

import os
import sys

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))
AGENT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(AGENT))

from agent.access_db import InMemoryAccessDatabase


def test_update_rows_updates_matching_key_only():
    db = InMemoryAccessDatabase({"t": [
        {"id": 1, "UNIT_CODE": "", "BASE_PRICE": 100},
        {"id": 2, "UNIT_CODE": "", "BASE_PRICE": 200},
    ]})
    n = db.update_rows("t", "id", [
        {"id": 1, "UNIT_CODE": "A-1", "BASE_PRICE": 111},
        {"id": 2, "UNIT_CODE": "A-2", "BASE_PRICE": 222},
    ])
    assert n == 2
    rows = {r["id"]: r for r in db.tables["t"]}
    assert rows[1]["UNIT_CODE"] == "A-1" and rows[1]["BASE_PRICE"] == 111
    assert rows[2]["UNIT_CODE"] == "A-2"


def test_update_rows_never_inserts_unmatched_key():
    db = InMemoryAccessDatabase({"t": [{"id": 1, "UNIT_CODE": ""}]})
    n = db.update_rows("t", "id", [{"id": 999, "UNIT_CODE": "NOPE"}])
    assert n == 0
    assert len(db.tables["t"]) == 1        # no row added
    assert db.tables["t"][0]["UNIT_CODE"] == ""


def test_update_rows_where_targets_composite_key_only():
    # Two rows share id=1778; only the composite (Project,Block,Flat_No) is unique.
    db = InMemoryAccessDatabase({"t": [
        {"id": 1778, "Project": 14, "Block": "A", "Flat_No": "204", "UNIT_CODE": ""},
        {"id": 1778, "Project": 19, "Block": "H", "Flat_No": "105", "UNIT_CODE": ""},
    ]})
    n = db.update_rows_where("t", ["Project", "Block", "Flat_No"], [
        {"Project": 14, "Block": "A", "Flat_No": "204", "UNIT_CODE": "OB-A-204"},
    ])
    assert n == 1
    rows = {(r["Project"], r["Flat_No"]): r for r in db.tables["t"]}
    assert rows[(14, "204")]["UNIT_CODE"] == "OB-A-204"
    assert rows[(19, "105")]["UNIT_CODE"] == ""      # the other id=1778 row untouched


def test_update_rows_where_never_inserts():
    db = InMemoryAccessDatabase({"t": [{"Project": 14, "Block": "A", "Flat_No": "204", "UNIT_CODE": ""}]})
    n = db.update_rows_where("t", ["Project", "Block", "Flat_No"],
                             [{"Project": 99, "Block": "Z", "Flat_No": "1", "UNIT_CODE": "X"}])
    assert n == 0
    assert len(db.tables["t"]) == 1
