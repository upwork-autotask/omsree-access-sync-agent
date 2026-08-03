"""Outbound FK resolution: a child table's foreign key holds a WEB id, but the parent
now lets Access own its primary key. `_resolve_fk_columns` translates the web value to
the Access-assigned id by looking it up in the parent Access table via its source-id
column. Rows whose FK can't be resolved (parent not in Access yet) are dropped.

crm_field syntax: ``fk:<pg_col>@<access_table>.<match_col>`` on a field whose
`access_column` is the FK column (and the parent's returned PK column).
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


# --- _pg_rows_for: an fk: field SELECTs its inner pg column, stores raw value ------

def test_two_access_columns_from_one_pg_column(engine):
    # The booking's application id (web customer_id) must land in BOTH the match key
    # (WEB_SOURCE_ID) and the FK column (CUSTOMER_ID). One web column -> two Access columns.
    fields = [
        _FakeField("WEB_SOURCE_ID", "customer_id", role="key"),
        _FakeField("CUSTOMER_ID", "customer_id"),
    ]
    tm = _FakeMapping("tbl_Property_Details", "src", fields)
    conn = _FakeConn(["customer_id"], [(168,)])
    rows, cols = engine._pg_rows_for(conn, tm)
    assert rows == [{"WEB_SOURCE_ID": 168, "CUSTOMER_ID": 168}]
    assert set(cols) >= {"WEB_SOURCE_ID", "CUSTOMER_ID"}


def test_pg_rows_for_selects_fk_source_and_stores_raw(engine):
    fields = [
        _FakeField("WEB_SOURCE_ID", "property_details_id", role="key"),
        _FakeField("CUSTOMER_ID", "fk:customer_id@tbl_Customer.CUSTOMER_WEB_ID"),
    ]
    tm = _FakeMapping("tbl_Property_Details", "src", fields)
    conn = _FakeConn(["property_details_id", "customer_id"], [(5622, 166)])
    rows, cols = engine._pg_rows_for(conn, tm)
    # raw web customer id lands under the Access FK column, unresolved for now
    assert rows == [{"WEB_SOURCE_ID": 5622, "CUSTOMER_ID": 166}]
    assert "CUSTOMER_ID" in cols


# --- _resolve_fk_columns: translate raw web id -> Access-assigned id ---------------

def _mapping_with_customer_fk():
    return _FakeMapping("tbl_Property_Details", "src", [
        _FakeField("WEB_SOURCE_ID", "property_details_id", role="key"),
        _FakeField("CUSTOMER_ID", "fk:customer_id@tbl_Customer.CUSTOMER_WEB_ID"),
    ])


def test_resolve_fk_translates_via_parent_lookup(engine):
    from agent.access_db import InMemoryAccessDatabase
    db = InMemoryAccessDatabase({"tbl_Customer": [
        {"CUSTOMER_WEB_ID": 166, "CUSTOMER_ID": 166},
        {"CUSTOMER_WEB_ID": 167, "CUSTOMER_ID": 900001},  # Access re-assigned this one
    ]})
    rows = [
        {"WEB_SOURCE_ID": 5622, "CUSTOMER_ID": 166},
        {"WEB_SOURCE_ID": 5623, "CUSTOMER_ID": 167},
    ]
    resolved, skipped = engine._resolve_fk_columns(db, _mapping_with_customer_fk(), rows)
    assert skipped == 0
    assert {"WEB_SOURCE_ID": 5622, "CUSTOMER_ID": 166} in resolved
    assert {"WEB_SOURCE_ID": 5623, "CUSTOMER_ID": 900001} in resolved  # translated


def test_resolve_fk_drops_orphan_when_parent_absent(engine):
    from agent.access_db import InMemoryAccessDatabase
    db = InMemoryAccessDatabase({"tbl_Customer": [
        {"CUSTOMER_WEB_ID": 166, "CUSTOMER_ID": 166},
    ]})
    rows = [
        {"WEB_SOURCE_ID": 5622, "CUSTOMER_ID": 166},
        {"WEB_SOURCE_ID": 5623, "CUSTOMER_ID": 999},  # no such customer in Access
    ]
    resolved, skipped = engine._resolve_fk_columns(db, _mapping_with_customer_fk(), rows)
    assert skipped == 1
    assert resolved == [{"WEB_SOURCE_ID": 5622, "CUSTOMER_ID": 166}]


def test_resolve_fk_noop_without_fk_fields(engine):
    from agent.access_db import InMemoryAccessDatabase
    db = InMemoryAccessDatabase({})
    tm = _FakeMapping("tbl_Customer", "src", [
        _FakeField("CUSTOMER_WEB_ID", "customer_id", role="key"),
        _FakeField("CUSTOMER_NAME_1ST", "customer_name_1st"),
    ])
    rows = [{"CUSTOMER_WEB_ID": 166, "CUSTOMER_NAME_1ST": "A"}]
    resolved, skipped = engine._resolve_fk_columns(db, tm, rows)
    assert skipped == 0
    assert resolved == rows
