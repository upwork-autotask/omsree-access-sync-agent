"""Sync-agent health verdict for the watchdog (see the check_health command).

Kept pure (no DB/IO) so it's easy to test: callers pass the latest run and the
latest *successful* run, and we say whether to alert.
"""

from __future__ import annotations


def verdict(last_run, last_ok_run, now, stale_minutes):
    """Return (healthy: bool, reason: str).

    last_run    = most recent SyncRun of any status, or None.
    last_ok_run = most recent SyncRun whose status is ok/dry-run, or None.
    Unhealthy when: nothing has run, the latest run FAILED, or no successful run
    within `stale_minutes` (which also catches the agent silently stopping).
    """
    if last_run is None:
        return False, "the sync has never run"
    if last_run.status == "error":
        err = getattr(last_run, "error_message", "") or "see logs"
        return False, f"last sync FAILED at {last_run.started_at:%Y-%m-%d %H:%M} UTC: {err}"
    if last_ok_run is None:
        return False, "no successful sync on record"
    age_min = (now - last_ok_run.started_at).total_seconds() / 60.0
    if age_min > stale_minutes:
        return False, (f"no successful sync in {int(age_min)} min "
                       f"(last OK {last_ok_run.started_at:%Y-%m-%d %H:%M} UTC)")
    return True, f"OK - last successful sync {last_ok_run.started_at:%Y-%m-%d %H:%M} UTC"
