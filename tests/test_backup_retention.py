"""Backup retention (keep newest N) and min-interval (back up at most every X min),
so a frequent sync cadence doesn't pile up unlimited backups.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent import access_db


def _stamp(dt):
    return dt.strftime("%Y%m%d-%H%M%S")


def _make_backups(dirp, stamps):
    for s in stamps:
        (dirp / f"CRM.backup-{s}.accdb").write_bytes(b"x")


def test_prune_keeps_newest_n(tmp_path):
    db = tmp_path / "CRM.accdb"; db.write_bytes(b"db")
    _make_backups(tmp_path, ["20260101-000000", "20260102-000000", "20260103-000000",
                             "20260104-000000", "20260105-000000"])
    removed = access_db._prune_backups(db, keep=2)
    remaining = [p.name for p in access_db._list_backups(db)]
    assert removed == 3
    assert remaining == ["CRM.backup-20260104-000000.accdb", "CRM.backup-20260105-000000.accdb"]


def test_prune_keep_zero_is_unlimited(tmp_path):
    db = tmp_path / "CRM.accdb"; db.write_bytes(b"db")
    _make_backups(tmp_path, ["20260101-000000", "20260102-000000"])
    assert access_db._prune_backups(db, keep=0) == 0
    assert len(access_db._list_backups(db)) == 2


def test_backup_skips_within_interval(tmp_path):
    db = tmp_path / "CRM.accdb"; db.write_bytes(b"db")
    recent = _stamp(datetime.now(timezone.utc) - timedelta(minutes=1))
    _make_backups(tmp_path, [recent])
    # a backup exists from 1 min ago; interval 30 min -> skip, no new file
    assert access_db.perform_backup(db, min_interval_minutes=30, keep=0) is None
    assert len(access_db._list_backups(db)) == 1


def test_backup_creates_after_interval_and_prunes(tmp_path):
    db = tmp_path / "CRM.accdb"; db.write_bytes(b"db")
    old = _stamp(datetime.now(timezone.utc) - timedelta(hours=2))
    _make_backups(tmp_path, ["20260101-000000", "20260102-000000", old])
    p = access_db.perform_backup(db, min_interval_minutes=30, keep=2)
    assert p is not None and p.exists()
    # 3 old + 1 new = 4, pruned to keep=2
    assert len(access_db._list_backups(db)) == 2
