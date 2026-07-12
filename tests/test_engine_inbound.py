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
