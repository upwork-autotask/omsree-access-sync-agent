"""Tests for the access->web (inbound) UPDATE-only SQL builder. Django-gated."""

import os
import sys

import pytest

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))

django = pytest.importorskip("django")


@pytest.fixture(scope="module")
def build_update_sql():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
    django.setup()
    from syncadmin.services.engine import _build_update_sql
    return _build_update_sql


def test_update_sql_sets_only_non_key_columns(build_update_sql):
    sql = build_update_sql("tbl_Customer", "customer_id",
                           ["customer_id", "customer_name_1st", "primary_contact_no"])
    assert sql.startswith('UPDATE "tbl_Customer" SET ')
    assert '"customer_name_1st" = %s' in sql
    assert '"primary_contact_no" = %s' in sql
    assert 'WHERE "customer_id" = %s' in sql
    # the key must never appear in the SET clause
    assert '"customer_id" = %s,' not in sql
    assert 'SET "customer_id"' not in sql


def test_update_sql_schema_qualified(build_update_sql):
    sql = build_update_sql("sync.unit", "unit_id", ["unit_id", "status"])
    assert sql == 'UPDATE "sync"."unit" SET "status" = %s WHERE "unit_id" = %s'


@pytest.fixture(scope="module")
def access_rows_for():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
    django.setup()
    from syncadmin.services.engine import _access_rows_for
    return _access_rows_for


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def read_rows(self, table, cols):
        return self._rows


class _FakeField:
    def __init__(self, access_column, crm_field, value_map=""):
        self.access_column = access_column
        self.crm_field = crm_field
        self.value_map = value_map
        self.is_active = True


class _FakeMapping:
    def __init__(self, fields, key_column):
        self._fields = fields
        self.key_column = key_column
        self.access_table = "tbl_Property_Details"

    @property
    def active_fields(self):
        return self._fields


def test_value_map_whitelist_translates_and_skips(access_rows_for):
    import json
    fields = [
        _FakeField("PROPERTY_DETAILS_ID", "id"),
        _FakeField("PROPERTY_STATUS_ID", "status", json.dumps({"3": "available"})),
    ]
    tm = _FakeMapping(fields, "PROPERTY_DETAILS_ID")
    db = _FakeDB([
        {"PROPERTY_DETAILS_ID": 1, "PROPERTY_STATUS_ID": 3},   # CANCELLED -> available
        {"PROPERTY_DETAILS_ID": 2, "PROPERTY_STATUS_ID": 1},   # BOOKED    -> skipped
        {"PROPERTY_DETAILS_ID": 3, "PROPERTY_STATUS_ID": None},  # NULL     -> skipped
    ])
    rows, crm_key, crm_cols = access_rows_for(db, tm)
    assert crm_key == "id"
    assert rows == [{"id": 1, "status": "available"}]  # only the cancelled row flows
