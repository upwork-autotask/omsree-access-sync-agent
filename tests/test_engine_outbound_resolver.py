"""Tests for the web->access natural-key UPDATE resolver (tbl_PropertyDefaults).

Web units are matched to EXISTING Access rows by project+block+flat and UPDATEd in
place by that COMPOSITE key (never by the non-unique `id`), only when a mapped value
changes. Web units with no Access row are reported, never inserted. Pure function.
"""

import os
import sys

import pytest

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))

django = pytest.importorskip("django")


@pytest.fixture(scope="module")
def resolve():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
    django.setup()
    from syncadmin.services.engine import resolve_defaults_updates
    return resolve_defaults_updates


# Existing Access rows (note: id is intentionally NON-unique -- 1778 repeats -- to
# prove the resolver keys on Project+Block+Flat_No, not id).
ACC = [
    {"id": 1778, "Project": 14, "Block": "A", "Flat_No": "204", "UNIT_CODE": "", "BASE_PRICE": 0},
    {"id": 1778, "Project": 19, "Block": "H", "Flat_No": "105", "UNIT_CODE": "", "BASE_PRICE": 0},
    {"id": 3315, "Project": 20, "Block": "-", "Flat_No": "804", "UNIT_CODE": "OLD", "BASE_PRICE": 5000},
    {"id": 40, "Project": 21, "Block": "A", "Flat_No": "101", "UNIT_CODE": "", "BASE_PRICE": 0},
    {"id": 41, "Project": 21, "Block": "A", "Flat_No": "101", "UNIT_CODE": "", "BASE_PRICE": 0},  # dup key
]
CROSSWALK = {"26": "14", "31": "19", "32": "20", "33": "21"}  # web pid -> Access Project
SET_MAP = {"code": "UNIT_CODE", "base_rate": "BASE_PRICE"}
COMMON = dict(project_crm="property_id", block_crm="block_name", flat_crm="flat_no",
              set_map=SET_MAP, crosswalk=CROSSWALK, key_access_cols=["Project", "Block", "Flat_No"])


def _web(pid, blk, flat, code, rate):
    return {"property_id": pid, "block_name": blk, "flat_no": flat,
            "code": code, "base_rate": rate}


def test_two_units_with_same_id_go_to_correct_rows_by_composite_key(resolve):
    # The exact bug: Brilliance A-204 and Gallaxy H-105 share Access id 1778.
    rows = [_web(26, "A", "204", "OB-A-204", 7200),   # -> Project 14
            _web(31, "H", "105", "OGL-H-105", 6000)]  # -> Project 19
    writes, report = resolve(rows, ACC, **COMMON)
    assert {"Project": 14, "Block": "A", "Flat_No": "204", "UNIT_CODE": "OB-A-204", "BASE_PRICE": 7200} in writes
    assert {"Project": 19, "Block": "H", "Flat_No": "105", "UNIT_CODE": "OGL-H-105", "BASE_PRICE": 6000} in writes
    assert report["updated"] == 2


def test_unchanged_row_is_not_written(resolve):
    rows = [_web(32, "-", "804", "OLD", 5000)]  # matches ACC row 3315 exactly
    writes, report = resolve(rows, ACC, **COMMON)
    assert writes == []
    assert report["unchanged"] == 1


def test_only_changed_columns_are_written(resolve):
    rows = [_web(32, "-", "804", "NEW", 5000)]  # code changes, price same
    writes, report = resolve(rows, ACC, **COMMON)
    assert writes == [{"Project": 20, "Block": "-", "Flat_No": "804", "UNIT_CODE": "NEW"}]


def test_ambiguous_access_key_is_skipped(resolve):
    rows = [_web(33, "A", "101", "OP-A-101", 8000)]  # (21,A,101) has two Access rows
    writes, report = resolve(rows, ACC, **COMMON)
    assert writes == []
    assert report["ambiguous"] == 1


def test_no_project_and_no_row_are_reported(resolve):
    rows = [_web(99, "A", "204", "X", 1),      # pid not in crosswalk
            _web(26, "Z", "999", "Y", 1)]      # right project, no such Access row
    writes, report = resolve(rows, ACC, **COMMON)
    assert writes == []
    assert report["no_project"] == 1
    assert report["no_row"] == 1
