"""_pg_rows_for applies a field value_map (web value -> Access value) on outbound,
as a strict whitelist (unmapped values skip the row, not write a wrong id)."""

import json
import os
import sys

import pytest

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))

django = pytest.importorskip("django")


@pytest.fixture(scope="module")
def pg_rows_for():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
    django.setup()
    from syncadmin.services.engine import _pg_rows_for
    return _pg_rows_for


class _FakeField:
    def __init__(self, access_column, crm_field, value_map="", role="sync"):
        self.access_column = access_column
        self.crm_field = crm_field
        self.value_map = value_map
        self.role = role
        self.is_active = True


class _FakeMapping:
    def __init__(self, fields):
        self._fields = fields
        self.access_table = "tbl_Property_Details"
        self.pg_table = "src"

    @property
    def active_fields(self):
        return self._fields


class _FakeCursor:
    def __init__(self, colnames, rows):
        self._colnames = colnames
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


def test_property_id_is_translated_via_crosswalk(pg_rows_for):
    fields = [
        _FakeField("PROPERTY_DETAILS_ID", "property_details_id", role="key"),
        _FakeField("PROPERTY_ID", "property_id", json.dumps({"26": 14, "20": 7})),
    ]
    tm = _FakeMapping(fields)
    conn = _FakeConn(["property_details_id", "property_id"],
                     [(5622, 26), (5001, 20)])
    rows, cols = pg_rows_for(conn, tm)
    assert {"PROPERTY_DETAILS_ID": 5622, "PROPERTY_ID": 14} in rows   # Brilliance, not 26/Glory
    assert {"PROPERTY_DETAILS_ID": 5001, "PROPERTY_ID": 7} in rows


def test_unmapped_project_skips_the_row(pg_rows_for):
    fields = [
        _FakeField("PROPERTY_DETAILS_ID", "property_details_id", role="key"),
        _FakeField("PROPERTY_ID", "property_id", json.dumps({"26": 14})),
    ]
    tm = _FakeMapping(fields)
    conn = _FakeConn(["property_details_id", "property_id"],
                     [(5622, 26), (7777, 39)])   # 39 (Madhuban) not in crosswalk
    rows, cols = pg_rows_for(conn, tm)
    assert rows == [{"PROPERTY_DETAILS_ID": 5622, "PROPERTY_ID": 14}]  # 39 dropped
