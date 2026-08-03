"""Tests for the access->web booking-status natural-key resolver.

The resolver matches Access booking rows (which carry Access-native ids that mean
nothing to the web) to web unit ids via a project crosswalk + (block, flat), then
decides each unit's status. Pure function -> no DB needed.
"""

import os
import sys
from datetime import datetime

import pytest

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))

django = pytest.importorskip("django")


@pytest.fixture(scope="module")
def resolve():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
    django.setup()
    from syncadmin.services.engine import resolve_booking_status
    return resolve_booking_status


# a small web inventory: (web_property_id, block, flat) -> [unit_id, ...]
WEB = {
    (20, "A", "101"): [5001],
    (20, "A", "102"): [5002],
    (23, "B", "9"): [6001],
    (26, "C", "1003"): [7001],
    (31, "D", "1"): [8001, 8002],   # duplicate -> ambiguous
}
CROSSWALK = {"7": 20, "10": 23, "14": 26, "19": 31}
VMAP = {"1": "booked", "3": "available"}

COMMON = dict(
    project_col="PROPERTY_ID", block_col="BLOCK_NAME", flat_col="FLAT_NO",
    status_col="PROPERTY_STATUS_ID", status_vmap=VMAP, crosswalk=CROSSWALK,
    web_lookup=WEB, active_col="ISACTIVE",
    modified_col="MODIFIED_DATE", created_col="CREATED_DATE",
    crm_key="id", status_crm="status",
)


def _row(pid, blk, flat, sid, active=True, mod=None, cre=None):
    return {"PROPERTY_ID": pid, "BLOCK_NAME": blk, "FLAT_NO": flat,
            "PROPERTY_STATUS_ID": sid, "ISACTIVE": active,
            "MODIFIED_DATE": mod, "CREATED_DATE": cre}


def test_booked_and_cancelled_translate_to_web_units(resolve):
    rows = [
        _row(7, "A", "101", 1),   # booked  -> unit 5001
        _row(7, "A", "102", 3),   # cancel  -> unit 5002 available
        _row(14, "C", "1003", 1), # booked  -> unit 7001
    ]
    incoming, report = resolve(rows, **COMMON)
    assert {"id": 5001, "status": "booked"} in incoming
    assert {"id": 5002, "status": "available"} in incoming
    assert {"id": 7001, "status": "booked"} in incoming
    assert report["resolved"] == 3


def test_status_not_in_whitelist_is_skipped(resolve):
    rows = [_row(7, "A", "101", 2),    # HANDED OVER -> skip
            _row(7, "A", "102", None)]  # NULL       -> skip
    incoming, report = resolve(rows, **COMMON)
    assert incoming == []
    assert report["skipped_status"] == 2


def test_inactive_rows_are_ignored(resolve):
    rows = [_row(7, "A", "101", 1, active=False)]  # booked but ISACTIVE False
    incoming, report = resolve(rows, **COMMON)
    assert incoming == []
    assert report["skipped_inactive"] == 1


def test_unknown_project_is_reported(resolve):
    rows = [_row(999, "A", "101", 1)]  # project not in crosswalk
    incoming, report = resolve(rows, **COMMON)
    assert incoming == []
    assert report["no_project"] == 1


def test_no_matching_unit_is_reported(resolve):
    rows = [_row(7, "Z", "999", 1)]  # right project, no such block/flat
    incoming, report = resolve(rows, **COMMON)
    assert incoming == []
    assert report["no_unit"] == 1


def test_ambiguous_unit_is_reported_not_written(resolve):
    rows = [_row(19, "D", "1", 1)]  # (31,D,1) maps to two web units
    incoming, report = resolve(rows, **COMMON)
    assert incoming == []
    assert report["ambiguous_unit"] == 1


def test_conflicting_records_latest_modified_wins(resolve):
    rows = [
        _row(7, "A", "101", 3, mod=datetime(2026, 8, 2, 20, 34)),  # cancelled earlier
        _row(7, "A", "101", 1, mod=datetime(2026, 8, 2, 20, 53)),  # rebooked later
    ]
    incoming, report = resolve(rows, **COMMON)
    assert incoming == [{"id": 5001, "status": "booked"}]
    assert report["resolved_conflict"] == 1


def test_fresh_rebooking_created_beats_older_modified_cancel(resolve):
    # Real A-406 case: unit was booked, cancelled (record modified at 20:34), then
    # re-booked by someone else (fresh record, modified=None, created 20:53).
    # Effective time: rebooking 20:53 > cancel 20:34 -> current status is booked.
    rows = [
        _row(7, "A", "101", 3,
             mod=datetime(2026, 8, 2, 20, 34), cre=datetime(2025, 8, 20, 16, 22)),
        _row(7, "A", "101", 1,
             mod=None, cre=datetime(2026, 8, 2, 20, 53)),
    ]
    incoming, report = resolve(rows, **COMMON)
    assert incoming == [{"id": 5001, "status": "booked"}]
    assert report["resolved_conflict"] == 1


def test_conflicting_records_equal_timestamps_are_skipped(resolve):
    ts = datetime(2024, 9, 5, 12, 24, 1)
    rows = [
        _row(7, "A", "101", 3, mod=ts),
        _row(7, "A", "101", 1, mod=ts),
    ]
    incoming, report = resolve(rows, **COMMON)
    assert incoming == []            # unresolvable tie -> not written
    assert report["conflict_tie"] == 1


def test_same_unit_same_status_collapses_to_one_write(resolve):
    rows = [_row(7, "A", "101", 1), _row(7, "A", "101", 1)]
    incoming, report = resolve(rows, **COMMON)
    assert incoming == [{"id": 5001, "status": "booked"}]
    assert report["resolved"] == 1
