"""Mapping-driven sync engine for the control panel.

web -> access: for each active TableMapping (direction web2access), pull the active
mapped columns from the CRM PostgreSQL table, translate crm_field -> access_column,
diff against the current Access rows (agent.diff.compute_diff), and upsert the changes
into Access (agent.access_db) inside a transaction. Web always wins.

Runs are recorded as SyncRun rows; failures raise (the caller logs + emails).
"""

from __future__ import annotations

import logging
import time

from django.utils import timezone

from agent.diff import compute_diff
from ..models import AgentSettings, SyncRun, TableMapping
from . import connections, mailer

logger = logging.getLogger("agent")


class EngineError(Exception):
    pass


def _pg_rows_for(settings, tm: TableMapping):
    """Pull active mapped columns for one table mapping from PostgreSQL.

    Returns rows keyed by Access column names (already translated), ready for the
    diff against Access.
    """
    fields = list(tm.active_fields)
    if not fields:
        return [], []
    # crm_field -> access_column translation map
    crm_to_access = {f.crm_field: f.access_column for f in fields}
    crm_cols = list(crm_to_access.keys())

    source_table = tm.pg_table or tm.access_table
    if "." in source_table:
        schema, name = source_table.split(".", 1)
        qualified = f'"{schema}"."{name}"'
    else:
        qualified = f'"{source_table}"'
    col_sql = ", ".join(f'"{c}"' for c in crm_cols)

    conn = connections._pg_connect(
        settings.pg_host, settings.pg_port, settings.pg_dbname,
        settings.pg_user, settings.pg_password, settings.pg_sslmode,
    )
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {col_sql} FROM {qualified}")
        colnames = [d[0] for d in cur.description]
        rows = []
        for raw in cur.fetchall():
            crm_row = dict(zip(colnames, raw))
            access_row = {crm_to_access[c]: v for c, v in crm_row.items() if c in crm_to_access}
            rows.append(access_row)
        # access column names actually present
        access_cols = [crm_to_access[c] for c in crm_cols]
        return rows, access_cols
    finally:
        conn.close()


def run_web_to_access(trigger: str = "manual", dry_run: bool | None = None) -> SyncRun:
    settings = AgentSettings.get_solo()
    dry = settings.dry_run if dry_run is None else dry_run
    run = SyncRun.objects.create(
        trigger=trigger, direction="web2access",
        status="dry-run" if dry else "ok",
    )
    t0 = time.monotonic()
    detail_lines: list[str] = []
    rows_written = 0
    tables_processed = 0
    backed_up = False

    try:
        if not settings.web_to_access_enabled:
            raise EngineError("web -> access syncing is turned off.")
        if not settings.access_db_path:
            raise EngineError("No Access DB configured (Connections screen).")

        from agent.access_db import PyodbcAccessDatabase

        db = PyodbcAccessDatabase(settings.access_db_path, settings.access_db_password)
        try:
            mappings = TableMapping.objects.filter(direction="web2access", is_active=True)
            if not mappings:
                detail_lines.append("No active web->access table mappings.")
            for tm in mappings:
                incoming, access_cols = _pg_rows_for(settings, tm)
                if not access_cols:
                    detail_lines.append(f"{tm.access_table}: no active fields, skipped")
                    continue
                existing = db.read_rows(tm.access_table, access_cols)
                diff = compute_diff(tm.access_table, tm.key_column, existing, incoming)
                tables_processed += 1
                detail_lines.append(diff.summary())
                logger.info("diff %s", diff.summary())

                if dry or not diff.has_changes:
                    continue

                if not backed_up:
                    path = db.backup()
                    if path:
                        detail_lines.append(f"backup: {path}")
                    backed_up = True

                rows = [c.new_row for c in diff.inserts] + [c.new_row for c in diff.updates]
                with db.transaction():
                    written = db.upsert_rows(tm.access_table, tm.key_column, rows)
                rows_written += written
                detail_lines.append(f"{tm.access_table}: wrote {written} row(s)")
                logger.info("wrote %d row(s) to %s", written, tm.access_table)
        finally:
            db.close()

        run.status = "dry-run" if dry else "ok"
        run.rows_written = rows_written
        run.tables_processed = tables_processed
        run.detail = "\n".join(detail_lines)
    except Exception as exc:
        run.status = "error"
        run.error_message = str(exc)
        run.detail = "\n".join(detail_lines)
        logger.error("web->access sync failed: %s", exc)
        mailer.send_alert(
            settings,
            "OmSree Sync Agent — sync FAILED",
            f"web->access sync failed at {timezone.now()}:\n\n{exc}\n\n" + "\n".join(detail_lines[-30:]),
        )
    finally:
        run.finished_at = timezone.now()
        run.duration_ms = int((time.monotonic() - t0) * 1000)
        run.save()

    return run


def run_scheduled() -> None:
    """Entry point the APScheduler job calls."""
    settings = AgentSettings.get_solo()
    if settings.web_to_access_enabled:
        run_web_to_access(trigger="scheduled")
    # access -> web (inbound) intentionally not wired yet (Phase G).
