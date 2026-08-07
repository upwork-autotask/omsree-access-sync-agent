"""Health verdict: is the sync agent OK, or should we alert (failed / stale / dead)?"""

import os
import sys
from datetime import datetime, timedelta, timezone

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
django = pytest.importorskip("django")


@pytest.fixture(scope="module")
def health():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlpanel.settings")
    django.setup()
    from syncadmin.services import health as h
    return h


class _Run:
    def __init__(self, status, started_at, error_message=""):
        self.status = status
        self.started_at = started_at
        self.error_message = error_message


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def test_never_ran_is_unhealthy(health):
    ok, reason = health.verdict(None, None, NOW, stale_minutes=90)
    assert not ok and "never" in reason.lower()


def test_last_run_failed_is_unhealthy(health):
    r = _Run("error", NOW - timedelta(minutes=3), "tbl_x failed")
    ok, reason = health.verdict(r, None, NOW, stale_minutes=90)
    assert not ok and "fail" in reason.lower()


def test_stale_success_is_unhealthy(health):
    # last OK sync was 5 hours ago -> agent likely stopped
    ok_run = _Run("ok", NOW - timedelta(hours=5), "")
    ok, reason = health.verdict(ok_run, ok_run, NOW, stale_minutes=90)
    assert not ok and "no successful sync" in reason.lower()


def test_recent_success_is_healthy(health):
    ok_run = _Run("ok", NOW - timedelta(minutes=4), "")
    ok, reason = health.verdict(ok_run, ok_run, NOW, stale_minutes=90)
    assert ok and "ok" in reason.lower()


def test_dry_run_counts_as_success(health):
    dry = _Run("dry-run", NOW - timedelta(minutes=4), "")
    ok, reason = health.verdict(dry, dry, NOW, stale_minutes=90)
    assert ok
