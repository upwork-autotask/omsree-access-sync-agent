"""Computed outbound fields:
- ``scale:<pg_col>:<factor>`` multiplies a web value (web percentage 4.1 -> Access
  fraction 0.041, matching the office 0-1 convention).
- ``cumsum:<value_col>:<partition_col>:<order_col>`` fills a running total per group
  (CUMULATIVE_PCT = running sum of PERCENTAGE per booking, ordered by MILESTONE_NO).
"""

import os
import sys

import pytest

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

django = pytest.importorskip("django")


@pytest.fixture(scope="module")
def engine():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
    django.setup()
    from syncadmin.services import engine as eng
    return eng


class _FakeField:
    def __init__(self, access_column, crm_field, role="sync", value_map=""):
        self.access_column = access_column
        self.crm_field = crm_field
        self.role = role
        self.value_map = value_map
        self.is_active = True


class _FakeMapping:
    def __init__(self, access_table, pg_table, fields):
        self.access_table = access_table
        self.pg_table = pg_table
        self._fields = fields

    @property
    def active_fields(self):
        return self._fields


class _FakeCursor:
    def __init__(self, colnames, rows):
        self._rows = rows
        self.description = [(c,) for c in colnames]

    def execute(self, sql):
        return self

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, colnames, rows):
        self._c = _FakeCursor(colnames, rows)

    def cursor(self):
        return self._c


def test_scale_divides_web_percentage_to_fraction(engine):
    fields = [
        _FakeField("PAYSCH_WEB_ID", "id", role="key"),
        _FakeField("PERCENTAGE", "scale:percentage:0.01"),
    ]
    tm = _FakeMapping("tbl_PropertyDetailsPaymentSchedule", "src", fields)
    conn = _FakeConn(["id", "percentage"], [(441, 4.1), (442, 10.0)])
    rows, cols = engine._pg_rows_for(conn, tm)
    by_id = {r["PAYSCH_WEB_ID"]: r["PERCENTAGE"] for r in rows}
    assert by_id[441] == pytest.approx(0.041)
    assert by_id[442] == pytest.approx(0.10)


def test_scale_leaves_none_untouched(engine):
    fields = [_FakeField("PERCENTAGE", "scale:percentage:0.01")]
    tm = _FakeMapping("t", "src", fields)
    conn = _FakeConn(["percentage"], [(None,)])
    rows, _ = engine._pg_rows_for(conn, tm)
    assert rows == [{"PERCENTAGE": None}]


def test_cumsum_running_total_per_group(engine):
    tm = _FakeMapping("tbl_PropertyDetailsPaymentSchedule", "src", [
        _FakeField("PROPERTY_DETAILS_ID", "property_details_id", role="key"),
        _FakeField("PERCENTAGE", "scale:percentage:0.01"),
        _FakeField("MILESTONE_NO", "milestone_no"),
        _FakeField("CUMULATIVE_PCT", "cumsum:PERCENTAGE:PROPERTY_DETAILS_ID:MILESTONE_NO"),
    ])
    rows = [
        {"PROPERTY_DETAILS_ID": 5623, "PERCENTAGE": 0.10, "MILESTONE_NO": 2},
        {"PROPERTY_DETAILS_ID": 5623, "PERCENTAGE": 0.041, "MILESTONE_NO": 1},
        {"PROPERTY_DETAILS_ID": 5621, "PERCENTAGE": 0.05, "MILESTONE_NO": 1},
        {"PROPERTY_DETAILS_ID": 5623, "PERCENTAGE": 0.10, "MILESTONE_NO": 3},
    ]
    engine._apply_cumulative(tm, rows)
    got = {(r["PROPERTY_DETAILS_ID"], r["MILESTONE_NO"]): round(r["CUMULATIVE_PCT"], 4) for r in rows}
    assert got[(5623, 1)] == 0.041
    assert got[(5623, 2)] == 0.141
    assert got[(5623, 3)] == 0.241
    assert got[(5621, 1)] == 0.05   # separate booking restarts the running total


def test_cumsum_noop_without_cumsum_fields(engine):
    tm = _FakeMapping("t", "src", [_FakeField("X", "x")])
    rows = [{"X": 1}]
    engine._apply_cumulative(tm, rows)
    assert rows == [{"X": 1}]
