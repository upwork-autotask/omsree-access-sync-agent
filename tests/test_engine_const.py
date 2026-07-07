"""Unit tests for the control-panel engine's constant-field parsing.

These need Django configured (the engine module imports the ORM models), so we
set the settings module and call django.setup() before importing. Skipped
cleanly if the control panel deps are unavailable.
"""

import os
import sys

import pytest

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))

django = pytest.importorskip("django")


@pytest.fixture(scope="module")
def parse_const():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
    django.setup()
    from syncadmin.services.engine import _parse_const
    return _parse_const


def test_parse_int(parse_const):
    assert parse_const("1") == 1
    assert parse_const("  42 ") == 42
    assert isinstance(parse_const("1"), int)


def test_parse_float(parse_const):
    assert parse_const("3.5") == 3.5


def test_parse_bool(parse_const):
    assert parse_const("true") is True
    assert parse_const("False") is False


def test_parse_string(parse_const):
    assert parse_const("Om Sree") == "Om Sree"
    assert parse_const("BUILDER") == "BUILDER"


def test_parse_blank_is_none(parse_const):
    assert parse_const("") is None
    assert parse_const("   ") is None
