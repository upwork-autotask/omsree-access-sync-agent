"""Applications outbound now keys on WEB_SOURCE_ID (the web booking PK) instead of
seeding an explicit PROPERTY_DETAILS_ID. Access owns PROPERTY_DETAILS_ID as a plain
AutoNumber, so office and web bookings can't collide, and re-syncing the same web
booking UPDATEs its row rather than inserting a duplicate.
"""

import os
import sys

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.access_db import InMemoryAccessDatabase


T = "tbl_Property_Details"
KEY = "WEB_SOURCE_ID"


def _booking(web_id, flat, status_active=True):
    # Note: no PROPERTY_DETAILS_ID -> Access assigns the AutoNumber itself.
    return {
        "WEB_SOURCE_ID": web_id,
        "PROPERTY_ID": 14,
        "BLOCK_NAME": "A",
        "FLAT_NO": flat,
        "ISACTIVE": status_active,
    }


def test_insert_omits_autonumber_key():
    db = InMemoryAccessDatabase({T: []})
    db.upsert_rows(T, KEY, [_booking(5622, "103")])
    rows = db.read_rows(T, ["WEB_SOURCE_ID", "PROPERTY_DETAILS_ID", "FLAT_NO"])
    assert len(rows) == 1
    # the write never carries PROPERTY_DETAILS_ID -> Access is left to auto-assign it
    assert "PROPERTY_DETAILS_ID" not in rows[0]
    assert rows[0]["WEB_SOURCE_ID"] == 5622


def test_resync_same_booking_updates_not_duplicates():
    db = InMemoryAccessDatabase({T: []})
    db.upsert_rows(T, KEY, [_booking(5622, "103")])
    # same web booking comes again with a changed field -> UPDATE in place
    changed = _booking(5622, "103")
    changed["FLAT_AMOUNT"] = 5_000_000
    db.upsert_rows(T, KEY, [changed])
    rows = db.read_rows(T, ["WEB_SOURCE_ID", "FLAT_AMOUNT"])
    assert len(rows) == 1  # no duplicate
    assert rows[0]["FLAT_AMOUNT"] == 5_000_000


def test_distinct_bookings_are_distinct_rows():
    db = InMemoryAccessDatabase({T: []})
    db.upsert_rows(T, KEY, [_booking(5621, "102"), _booking(5622, "103")])
    rows = db.read_rows(T, ["WEB_SOURCE_ID"])
    assert sorted(r["WEB_SOURCE_ID"] for r in rows) == [5621, 5622]


def test_backfilled_row_is_matched_not_reinserted():
    # legacy web row already present with WEB_SOURCE_ID backfilled to its old id
    db = InMemoryAccessDatabase({T: [
        {"PROPERTY_DETAILS_ID": 5622, "WEB_SOURCE_ID": 5622, "BLOCK_NAME": "A", "FLAT_NO": "103"},
    ]})
    db.upsert_rows(T, KEY, [_booking(5622, "103")])
    rows = db.read_rows(T, ["WEB_SOURCE_ID", "PROPERTY_DETAILS_ID"])
    assert len(rows) == 1                       # matched the backfilled row, no dup
    assert rows[0]["PROPERTY_DETAILS_ID"] == 5622  # original AutoNumber preserved
