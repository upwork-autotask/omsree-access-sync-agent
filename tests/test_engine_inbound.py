"""Tests for the access->web (inbound) SQL builder. Django-gated like the const test."""

import os
import sys

import pytest

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))

django = pytest.importorskip("django")


@pytest.fixture(scope="module")
def build_upsert_sql():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
    django.setup()
    from syncadmin.services.engine import _build_upsert_sql
    return _build_upsert_sql


def test_upsert_sql_public_table(build_upsert_sql):
    sql = build_upsert_sql("tbl_Customer", "customer_id", ["customer_id", "customer_name_1st"])
    assert 'INSERT INTO "tbl_Customer"' in sql
    assert "(%s, %s)" in sql
    assert 'ON CONFLICT ("customer_id") DO UPDATE SET' in sql
    # the key is not in the SET clause; the non-key column is
    assert '"customer_name_1st" = EXCLUDED."customer_name_1st"' in sql
    assert '"customer_id" = EXCLUDED' not in sql


def test_upsert_sql_schema_qualified(build_upsert_sql):
    sql = build_upsert_sql("sync.unit", "unit_id", ["unit_id", "status"])
    assert 'INSERT INTO "sync"."unit"' in sql
    assert 'ON CONFLICT ("unit_id")' in sql
