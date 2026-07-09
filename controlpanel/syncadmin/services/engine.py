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
import traceback as _traceback

from django.utils import timezone

from agent.diff import compute_diff
from ..models import AgentSettings, SyncRun, TableMapping
from . import connections, mailer

logger = logging.getLogger("agent")


class EngineError(Exception):
    pass


def _pg_connect_retry(settings, attempts: int = 3, base_delay: float = 2.0):
    """Open one PostgreSQL connection, retrying transient failures.

    A single shared connection per run (instead of one per table) means one TCP
    connect+auth to the remote server rather than nine — far fewer chances for a
    connection timeout over a slow/internet link.
    """
    last = None
    for i in range(attempts):
        try:
            conn = connections._pg_connect(
                settings.pg_host, settings.pg_port, settings.pg_dbname,
                settings.pg_user, settings.pg_password, settings.pg_sslmode,
                timeout=20,
            )
            # We only ever SELECT from PostgreSQL. Autocommit means a failed query
            # on one table does not leave the shared connection in an aborted
            # transaction that poisons every later table (InFailedSqlTransaction).
            conn.autocommit = True
            return conn
        except Exception as exc:  # transient network / server-busy
            last = exc
            logger.warning("PostgreSQL connect attempt %d/%d failed: %s", i + 1, attempts, exc)
            if i < attempts - 1:
                time.sleep(base_delay * (i + 1))
    raise EngineError(f"Could not connect to PostgreSQL after {attempts} attempts: {last}")


def _parse_const(raw: str):
    """Parse a `const:` literal into a Python value (int, float, bool, or str)."""
    raw = raw.strip()
    if raw == "":
        return None
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _pg_rows_for(conn, tm: TableMapping):
    """Pull active mapped columns for one table mapping from a shared PG connection.

    Returns rows keyed by Access column names (already translated), ready for the
    diff against Access. The connection is owned by the caller (not closed here).

    A field whose crm_field is ``const:<value>`` is treated as a constant: the
    literal is injected into every row instead of being SELECTed from the view.
    This fills Access-required columns that have no CRM source (e.g. a required
    lookup id that is uniform for all web rows).
    """
    fields = list(tm.active_fields)
    if not fields:
        return [], []
    # Split real (SELECTed) columns from injected constants.
    real_fields: dict[str, str] = {}   # crm_field -> access_column
    const_fields: dict[str, object] = {}  # access_column -> literal value
    for f in fields:
        cf = (f.crm_field or "").strip()
        if cf.lower().startswith("const:"):
            const_fields[f.access_column] = _parse_const(cf[len("const:"):])
        else:
            real_fields[cf] = f.access_column
    crm_cols = list(real_fields.keys())

    source_table = tm.pg_table or tm.access_table
    if "." in source_table:
        schema, name = source_table.split(".", 1)
        qualified = f'"{schema}"."{name}"'
    else:
        qualified = f'"{source_table}"'

    rows = []
    if crm_cols:  # there must be at least the key column to SELECT and diff on
        col_sql = ", ".join(f'"{c}"' for c in crm_cols)
        cur = conn.cursor()
        cur.execute(f"SELECT {col_sql} FROM {qualified}")
        colnames = [d[0] for d in cur.description]
        for raw in cur.fetchall():
            crm_row = dict(zip(colnames, raw))
            access_row = {real_fields[c]: v for c, v in crm_row.items() if c in real_fields}
            access_row.update(const_fields)  # stamp the constants onto every row
            rows.append(access_row)
    access_cols = list(real_fields.values()) + list(const_fields.keys())
    return rows, access_cols


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
        pg_conn = None
        try:
            # One shared PostgreSQL connection for the whole run (retried up-front).
            # If PG can't be reached at all, abort before touching Access.
            pg_conn = _pg_connect_retry(settings)
            mappings = TableMapping.objects.filter(direction="web2access", is_active=True)
            if not mappings:
                detail_lines.append("No active web->access table mappings.")
            table_errors: list[str] = []
            for tm in mappings:
                # Isolate each table: one table's failure must not abort the whole run.
                try:
                    incoming, access_cols = _pg_rows_for(pg_conn, tm)
                    if not access_cols:
                        detail_lines.append(f"{tm.access_table}: no active fields, skipped")
                        continue
                    # Safety guard: never upsert a row whose key is null/blank -- it
                    # cannot be matched against Access and would create a junk row
                    # with an empty key (e.g. web units not yet assigned a code).
                    key = tm.key_column
                    _before = len(incoming)
                    incoming = [r for r in incoming if str(r.get(key) or "").strip() != ""]
                    _skipped = _before - len(incoming)
                    if _skipped:
                        detail_lines.append(f"{tm.access_table}: skipped {_skipped} row(s) with blank {key}")
                        logger.warning("skipped %d blank-key row(s) for %s", _skipped, tm.access_table)
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

                    # After seeding explicit web IDs into an AutoNumber key, bump the
                    # table's AutoNumber seed past MAX(id) so an office-side insert in
                    # Access can't later reuse an ID that belongs to a web record.
                    if diff.inserts:
                        try:
                            seed = db.reseed_autonumber(tm.access_table, tm.key_column)
                            if seed is not None:
                                detail_lines.append(f"{tm.access_table}: AutoNumber reseeded to {seed}")
                                logger.info("reseeded %s AutoNumber -> %d", tm.access_table, seed)
                        except Exception as rexc:
                            detail_lines.append(f"{tm.access_table}: AutoNumber reseed skipped ({rexc})")
                            logger.warning("reseed failed on %s: %s", tm.access_table, rexc)
                except Exception as texc:
                    msg = f"{tm.access_table}: FAILED - {type(texc).__name__}: {texc}"
                    table_errors.append(msg)
                    detail_lines.append(msg)
                    logger.exception("table %s failed", tm.access_table)
                    # Clear any aborted transaction so a query error on this table
                    # cannot cascade into InFailedSqlTransaction on the next table.
                    if pg_conn is not None:
                        try:
                            pg_conn.rollback()
                        except Exception:
                            pass
                    # A dropped/broken PG connection poisons every later table on the
                    # same connection. Try to re-establish it; if PG is truly down,
                    # stop rather than logging the same timeout for the rest.
                    import psycopg2
                    if isinstance(texc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
                        try:
                            pg_conn.close()
                        except Exception:
                            pass
                        try:
                            pg_conn = _pg_connect_retry(settings)
                            detail_lines.append("reconnected to PostgreSQL")
                        except Exception as rexc:
                            detail_lines.append(f"PostgreSQL unreachable - aborting remaining tables: {rexc}")
                            break
        finally:
            if pg_conn is not None:
                try:
                    pg_conn.close()
                except Exception:
                    pass
            db.close()

        run.rows_written = rows_written
        run.tables_processed = tables_processed
        run.detail = "\n".join(detail_lines)
        if table_errors:
            # Some tables synced, some failed: surface as error but keep the successes.
            run.status = "error"
            run.error_message = (
                f"{len(table_errors)} of {len(mappings)} table(s) failed; "
                f"{tables_processed} synced. See detail."
            )
            run.traceback = "\n".join(table_errors)
            mailer.send_alert(
                settings,
                "OmSree Sync Agent - sync completed with errors",
                f"{run.error_message}\n\n" + "\n".join(detail_lines),
            )
        else:
            run.status = "dry-run" if dry else "ok"
    except Exception as exc:
        tb = _traceback.format_exc()
        run.status = "error"
        run.error_message = f"{type(exc).__name__}: {exc}"
        run.traceback = tb
        run.detail = "\n".join(detail_lines)
        # Full traceback to sync.log (logger.exception attaches it automatically).
        logger.exception("web->access sync failed: %s", exc)
        mailer.send_alert(
            settings,
            "OmSree Sync Agent — sync FAILED",
            f"web->access sync failed at {timezone.now()}:\n\n{run.error_message}\n\n"
            + "Progress:\n" + "\n".join(detail_lines[-30:])
            + "\n\nTraceback:\n" + tb,
        )
    finally:
        run.finished_at = timezone.now()
        run.duration_ms = int((time.monotonic() - t0) * 1000)
        run.save()

    return run


# --------------------------------------------------------------------------- #
# access -> web (inbound)
# --------------------------------------------------------------------------- #
def _access_rows_for(db, tm: TableMapping):
    """Read active mapped columns from the Access table, translated to CRM field
    names. Returns (rows keyed by crm_field, crm_key_field, crm_cols).

    Only 'real' fields flow (const: fields are outbound-only). Fields whose role
    keeps them in Access (is_active False) are never read, so KYC/no-leave columns
    can't travel back to the web.
    """
    fields = [f for f in tm.active_fields
              if not (f.crm_field or "").strip().lower().startswith("const:")]
    if not fields:
        return [], None, []
    access_to_crm = {f.access_column: f.crm_field for f in fields}
    access_cols = list(access_to_crm.keys())
    rows = db.read_rows(tm.access_table, access_cols)
    out = [{access_to_crm[c]: v for c, v in r.items() if c in access_to_crm} for r in rows]
    crm_key = access_to_crm.get(tm.key_column)
    crm_cols = list(access_to_crm.values())
    return out, crm_key, crm_cols


def _pg_read_rows(conn, table: str, cols: list[str]):
    """Read current rows from a CRM table/view (for diffing), keyed by crm_field."""
    if "." in table:
        schema, name = table.split(".", 1)
        qualified = f'"{schema}"."{name}"'
    else:
        qualified = f'"{table}"'
    col_sql = ", ".join(f'"{c}"' for c in cols)
    cur = conn.cursor()
    cur.execute(f"SELECT {col_sql} FROM {qualified}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _build_upsert_sql(target: str, key: str, cols: list[str]) -> str:
    """Parameterised INSERT ... ON CONFLICT (key) DO UPDATE for one CRM row."""
    if "." in target:
        schema, name = target.split(".", 1)
        qualified = f'"{schema}"."{name}"'
    else:
        qualified = f'"{target}"'
    collist = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c != key)
    return (f"INSERT INTO {qualified} ({collist}) VALUES ({placeholders}) "
            f'ON CONFLICT ("{key}") DO UPDATE SET {updates}')


def _pg_upsert_rows(conn, target: str, key: str, rows: list[dict]) -> int:
    """Upsert rows into a CRM table. Caller manages the transaction (commit)."""
    written = 0
    cur = conn.cursor()
    for row in rows:
        cols = list(row.keys())
        cur.execute(_build_upsert_sql(target, key, cols), [row[c] for c in cols])
        written += 1
    return written


def run_access_to_web(trigger: str = "manual", dry_run: bool | None = None) -> SyncRun:
    """Push agreed Access changes back to the CRM (access -> web).

    Mirrors run_web_to_access with the direction reversed: Access is the source,
    the CRM table is the target, and Access wins. Writes are transactional per
    table. Requires WRITE access to the CRM target (base table or an updatable
    view); until that is granted the live path will simply error per table, which
    is caught and reported. Defaults to whatever settings.dry_run says.
    """
    settings = AgentSettings.get_solo()
    dry = settings.dry_run if dry_run is None else dry_run
    run = SyncRun.objects.create(
        trigger=trigger, direction="access2web",
        status="dry-run" if dry else "ok",
    )
    t0 = time.monotonic()
    detail_lines: list[str] = []
    rows_written = 0
    tables_processed = 0

    try:
        # A dry-run writes nothing, so previewing inbound is always allowed. Only a
        # LIVE inbound run requires the access->web direction to be toggled on.
        if not dry and not settings.access_to_web_enabled:
            raise EngineError(
                "access -> web live writes are turned off. Toggle the direction ON to "
                "write (a dry-run is always allowed for previewing)."
            )
        if not settings.access_db_path:
            raise EngineError("No Access DB configured (Connections screen).")

        from agent.access_db import PyodbcAccessDatabase

        db = PyodbcAccessDatabase(settings.access_db_path, settings.access_db_password)
        pg_conn = None
        try:
            pg_conn = _pg_connect_retry(settings)
            # Only a live run needs to write; a dry-run stays read-only (autocommit)
            # so it can't accidentally change the CRM even if the toggle is off.
            if not dry:
                pg_conn.autocommit = False  # inbound writes are transactional per table
            mappings = TableMapping.objects.filter(direction="access2web", is_active=True)
            if not mappings:
                detail_lines.append("No active access->web table mappings.")
            table_errors: list[str] = []
            for tm in mappings:
                try:
                    incoming, crm_key, crm_cols = _access_rows_for(db, tm)
                    if not crm_key or not crm_cols:
                        detail_lines.append(f"{tm.access_table}: no active key/fields, skipped")
                        continue
                    target = tm.pg_table or tm.access_table
                    # Never push a row without a key.
                    incoming = [r for r in incoming if str(r.get(crm_key) or "").strip() != ""]
                    existing = _pg_read_rows(pg_conn, target, crm_cols)
                    diff = compute_diff(target, crm_key, existing, incoming)
                    tables_processed += 1
                    detail_lines.append(f"{tm.access_table} -> {target}: {diff.summary()}")
                    logger.info("inbound diff %s", diff.summary())

                    if dry or not diff.has_changes:
                        pg_conn.rollback()
                        continue

                    rows = [c.new_row for c in diff.inserts] + [c.new_row for c in diff.updates]
                    written = _pg_upsert_rows(pg_conn, target, crm_key, rows)
                    pg_conn.commit()
                    rows_written += written
                    detail_lines.append(f"{target}: wrote {written} row(s)")
                    logger.info("inbound wrote %d row(s) to %s", written, target)
                except Exception as texc:
                    try:
                        pg_conn.rollback()
                    except Exception:
                        pass
                    msg = f"{tm.access_table}: FAILED - {type(texc).__name__}: {texc}"
                    table_errors.append(msg)
                    detail_lines.append(msg)
                    logger.exception("inbound table %s failed", tm.access_table)
                    import psycopg2
                    if isinstance(texc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
                        try:
                            pg_conn.close()
                        except Exception:
                            pass
                        try:
                            pg_conn = _pg_connect_retry(settings)
                            pg_conn.autocommit = False
                            detail_lines.append("reconnected to PostgreSQL")
                        except Exception as rexc:
                            detail_lines.append(f"PostgreSQL unreachable - aborting remaining tables: {rexc}")
                            break
        finally:
            if pg_conn is not None:
                try:
                    pg_conn.close()
                except Exception:
                    pass
            db.close()

        run.rows_written = rows_written
        run.tables_processed = tables_processed
        run.detail = "\n".join(detail_lines)
        if table_errors:
            run.status = "error"
            run.error_message = (
                f"{len(table_errors)} of {len(mappings)} table(s) failed; "
                f"{tables_processed} synced. See detail."
            )
            run.traceback = "\n".join(table_errors)
            mailer.send_alert(
                settings,
                "OmSree Sync Agent - access->web completed with errors",
                f"{run.error_message}\n\n" + "\n".join(detail_lines),
            )
        else:
            run.status = "dry-run" if dry else "ok"
    except Exception as exc:
        tb = _traceback.format_exc()
        run.status = "error"
        run.error_message = f"{type(exc).__name__}: {exc}"
        run.traceback = tb
        run.detail = "\n".join(detail_lines)
        logger.exception("access->web sync failed: %s", exc)
        mailer.send_alert(
            settings,
            "OmSree Sync Agent - access->web FAILED",
            f"access->web sync failed at {timezone.now()}:\n\n{run.error_message}\n\n"
            + "Progress:\n" + "\n".join(detail_lines[-30:]) + "\n\nTraceback:\n" + tb,
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
    if settings.access_to_web_enabled:
        run_access_to_web(trigger="scheduled")
