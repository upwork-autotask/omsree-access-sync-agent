"""Sync orchestration.

Pulls web-origin changes from the CRM, computes the diff against the local
Access DB, and (unless dry-run) applies them inside a transaction, then acks.

Web is the source of truth: web-origin rows overwrite Access on the synced
columns. The agent never deletes Access rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .access_db import AccessDatabase
from .config import Config
from .crm_client import Batch, CrmClient
from .diff import TableDiff, compute_diff, format_diff
from .state import SyncState, utcnow_iso

logger = logging.getLogger("agent")


@dataclass
class SyncReport:
    dry_run: bool
    diffs: list[TableDiff] = field(default_factory=list)
    rows_written: int = 0
    cursor: str | None = None
    batches_processed: int = 0

    @property
    def total_changes(self) -> int:
        return sum(d.change_count for d in self.diffs)


class Syncer:
    def __init__(
        self,
        config: Config,
        crm: CrmClient,
        db: AccessDatabase,
        state: SyncState,
    ) -> None:
        self.config = config
        self.crm = crm
        self.db = db
        self.state = state

    def _columns_for(self, batch: Batch) -> list[str]:
        """Union of all columns appearing in a batch's rows (key included)."""
        seen: list[str] = []
        for row in batch.rows:
            for col in row:
                if col not in seen:
                    seen.append(col)
        if batch.key not in seen:
            seen.insert(0, batch.key)
        return seen

    def _diff_batch(self, batch: Batch) -> TableDiff:
        columns = self._columns_for(batch)
        existing = self.db.read_rows(batch.table, columns)
        return compute_diff(batch.table, batch.key, existing, batch.rows)

    def run_once(self, *, dry_run: bool | None = None) -> SyncReport:
        dry_run = self.config.dry_run if dry_run is None else dry_run
        report = SyncReport(dry_run=dry_run)

        tables = self.config.table_whitelist or None
        logger.info(
            "pulling outbound since cursor=%s tables=%s (dry_run=%s)",
            self.state.cursor,
            ",".join(tables) if tables else "ALL",
            dry_run,
        )

        backed_up = False
        cursor = self.state.cursor

        while True:
            resp = self.crm.fetch_outbound(cursor, tables)

            for batch in resp.batches:
                if self.config.table_whitelist and batch.table not in self.config.table_whitelist:
                    logger.warning("skipping non-whitelisted table from server: %s", batch.table)
                    continue

                diff = self._diff_batch(batch)
                report.diffs.append(diff)
                report.batches_processed += 1
                logger.info("diff %s", diff.summary())

                if dry_run:
                    if diff.has_changes:
                        logger.info("\n%s", format_diff(diff))
                    continue

                if not diff.has_changes:
                    continue

                # First write of the run: take a safety backup.
                if not backed_up:
                    path = self.db.backup()
                    if path:
                        logger.info("backup written: %s", path)
                    backed_up = True

                written = self._apply(batch, diff)
                report.rows_written += written

            cursor = resp.cursor or cursor
            if not resp.has_more:
                break

        report.cursor = cursor

        if dry_run:
            self.state.last_result = "dry-run"
            self.state.last_error = None
            self.state.last_sync = utcnow_iso()
            logger.info(
                "dry-run complete: %d change(s) across %d batch(es); nothing written",
                report.total_changes,
                report.batches_processed,
            )
            return report

        # Acknowledge and advance the cursor only after a successful write.
        if cursor and cursor != self.state.cursor:
            results = [
                {"table": d.table, "rows": d.change_count, "result": "ok"}
                for d in report.diffs
                if d.has_changes
            ]
            self.crm.post_ack(cursor, results)
            logger.info("acked cursor=%s", cursor)

        self.state.cursor = cursor
        self.state.last_sync = utcnow_iso()
        self.state.last_result = "ok"
        self.state.last_error = None
        self.state.rows_written = report.rows_written
        logger.info(
            "sync complete: %d row(s) written across %d batch(es)",
            report.rows_written,
            report.batches_processed,
        )
        return report

    def _apply(self, batch: Batch, diff: TableDiff) -> int:
        rows_to_write = [c.new_row for c in diff.inserts] + [c.new_row for c in diff.updates]
        if not rows_to_write:
            return 0
        with self.db.transaction():
            written = self.db.upsert_rows(batch.table, batch.key, rows_to_write)
        logger.info("wrote %d row(s) to %s", written, batch.table)
        return written
