"""Mapping-driven sync engine for the control panel.

web -> access: for each active TableMapping (direction web2access), pull the active
mapped columns from the CRM PostgreSQL table, translate crm_field -> access_column,
diff against the current Access rows (agent.diff.compute_diff), and upsert the changes
into Access (agent.access_db) inside a transaction. Web always wins.

Runs are recorded as SyncRun rows; failures raise (the caller logs + emails).
"""

from __future__ import annotations

import json
import logging
import time
import traceback as _traceback

from django.utils import timezone

from agent.diff import compute_diff
from ..models import AgentSettings, SyncRun, TableMapping
from . import connections, mailer

logger = logging.getLogger("agent")

# Web-originated rows are inserted into Access with their web id as the (AutoNumber)
# key. Office-created rows must never get an AutoNumber id that overlaps the web id
# range, or a later web sync would clobber the office row. After any web insert we
# reseed the AutoNumber to at least this floor, so office ids live permanently above
# every web id (web unit/customer ids are well under 10K today).
AUTONUMBER_FLOOR = 1_000_000


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


def _parse_fk(cf: str):
    """Parse ``fk:<pg_col>@<access_table>.<match_col>`` into its three parts.

    Example: ``fk:customer_id@tbl_Customer.CUSTOMER_WEB_ID`` selects web column
    ``customer_id`` and resolves it against Access ``tbl_Customer`` matching on
    ``CUSTOMER_WEB_ID`` -> returns (pg_col, access_table, match_col).
    """
    body = cf.strip()
    if body.lower().startswith("fk:"):
        body = body[len("fk:"):]
    pg_col, rest = body.split("@", 1)
    access_table, match_col = rest.rsplit(".", 1)
    return pg_col.strip(), access_table.strip(), match_col.strip()


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
    # Split real (SELECTed) columns from injected constants. A single web column may
    # feed MORE THAN ONE Access column (e.g. the booking's application id lands in both
    # WEB_SOURCE_ID and CUSTOMER_ID), so targets is a list, not a crm_field-keyed dict.
    targets: list[tuple] = []          # (crm_field, access_column, value_map_or_None, scale_or_None)
    const_fields: dict[str, object] = {}  # access_column -> literal value
    cumsum_cols: list[str] = []        # access columns filled later by _apply_cumulative
    for f in fields:
        cf = (f.crm_field or "").strip()
        if cf.lower().startswith("const:"):
            const_fields[f.access_column] = _parse_const(cf[len("const:"):])
        elif cf.lower().startswith("fk:"):
            # SELECT the inner web column and stash its raw value under this field's
            # Access column; _resolve_fk_columns turns it into the Access-assigned
            # parent id in a later pass. No value_map on fk fields.
            targets.append((_parse_fk(cf)[0], f.access_column, None, None))
        elif cf.lower().startswith("scale:"):
            # scale:<pg_col>:<factor> -- multiply the web value by a constant (e.g. a
            # web percentage 4.1 -> Access fraction 0.041 to match the office 0-1 scale).
            _, pg_col, factor = cf.split(":", 2)
            targets.append((pg_col.strip(), f.access_column, None, float(factor)))
        elif cf.lower().startswith("cumsum:"):
            # Computed post-hoc from sibling Access columns; not SELECTed here.
            cumsum_cols.append(f.access_column)
        else:
            vmap = None
            if f.value_map:
                try:
                    vmap = json.loads(f.value_map)
                except (ValueError, TypeError):
                    logger.warning("bad value_map JSON on %s.%s", tm.access_table, f.access_column)
            targets.append((cf, f.access_column, vmap, None))
    crm_cols = list(dict.fromkeys(t[0] for t in targets))  # distinct, order preserved

    source_table = tm.pg_table or tm.access_table
    if "." in source_table:
        schema, name = source_table.split(".", 1)
        qualified = f'"{schema}"."{name}"'
    else:
        qualified = f'"{source_table}"'

    rows = []
    skipped = 0
    if crm_cols:  # there must be at least the key column to SELECT and diff on
        col_sql = ", ".join(f'"{c}"' for c in crm_cols)
        cur = conn.cursor()
        cur.execute(f"SELECT {col_sql} FROM {qualified}")
        colnames = [d[0] for d in cur.description]
        for raw in cur.fetchall():
            crm_row = dict(zip(colnames, raw))
            access_row = {}
            drop = False
            for crm_field, access_col, vmap, scale in targets:
                if crm_field not in crm_row:
                    continue
                v = crm_row[crm_field]
                if vmap is not None:
                    # Translate a web value to the Access value (e.g. web property_id
                    # -> Access PROPERTY_ID). Strict whitelist: an unmapped value means
                    # we don't know the correct Access id, so skip the row rather than
                    # write a wrong one.
                    if v is not None and str(v) in vmap:
                        v = vmap[str(v)]
                    else:
                        drop = True
                        break
                if scale is not None and v is not None:
                    v = float(v) * scale
                access_row[access_col] = v
            if drop:
                skipped += 1
                continue
            access_row.update(const_fields)  # stamp the constants onto every row
            rows.append(access_row)
    if skipped:
        logger.warning("%s: skipped %d row(s) with a value not in a field value_map",
                       tm.access_table, skipped)
    access_cols = (list(dict.fromkeys(t[1] for t in targets))
                   + list(const_fields.keys()) + cumsum_cols)
    return rows, access_cols


def _apply_cumulative(tm, rows):
    """Fill ``cumsum:`` fields with a running total per group, computed from sibling
    Access columns already present on each row.

    crm_field ``cumsum:<value_col>:<partition_col>:<order_col>`` -> the target Access
    column becomes the running sum of ``value_col`` within each ``partition_col`` group,
    accumulated in ``order_col`` order (e.g. CUMULATIVE_PCT = running PERCENTAGE per
    booking, ordered by MILESTONE_NO). Operates in place; returns rows.
    """
    specs = []
    for f in tm.active_fields:
        cf = (f.crm_field or "").strip()
        if cf.lower().startswith("cumsum:"):
            _, val_col, part_col, order_col = cf.split(":", 3)
            specs.append((f.access_column, val_col.strip(), part_col.strip(), order_col.strip()))
    if not specs:
        return rows
    for target_col, val_col, part_col, order_col in specs:
        groups: dict = {}
        for r in rows:
            groups.setdefault(r.get(part_col), []).append(r)
        for grp in groups.values():
            grp.sort(key=lambda r: (r.get(order_col) is None, r.get(order_col)))
            running = 0.0
            for r in grp:
                running += float(r.get(val_col) or 0)
                r[target_col] = running
    return rows


def _resolve_fk_columns(db, tm, rows):
    """Resolve FK columns that carry a web id into the Access-assigned parent id.

    A field whose crm_field is ``fk:<pg_col>@<access_table>.<match_col>`` had its raw
    web value placed under its Access column by :func:`_pg_rows_for`. Here we look that
    value up in the parent Access table (``match_col`` == value) and replace it with the
    parent's own key (this field's Access column). This is required once the parent lets
    Access own its primary key (e.g. tbl_Customer.CUSTOMER_ID, tbl_Property_Details.
    PROPERTY_DETAILS_ID) so the child stores the *Access* id, not the web id.

    Rows whose parent is not yet in Access (FK orphan) are dropped and counted, matching
    the existing FK-integrity policy (a child is never written pointing at a missing
    parent). Returns ``(resolved_rows, skipped_count)``.
    """
    fk_fields = [f for f in tm.active_fields
                 if (f.crm_field or "").strip().lower().startswith("fk:")]
    if not fk_fields:
        return rows, 0

    lookups: dict[str, dict] = {}
    for f in fk_fields:
        _pg_col, access_table, match_col = _parse_fk(f.crm_field)
        ret_col = f.access_column
        pairs = db.read_rows(access_table, [match_col, ret_col])
        lookups[ret_col] = {
            str(p[match_col]): p[ret_col]
            for p in pairs if p.get(match_col) is not None
        }

    resolved = []
    skipped = 0
    for row in rows:
        drop = False
        for f in fk_fields:
            col = f.access_column
            raw = row.get(col)
            key = None if raw is None else str(raw)
            if key is not None and key in lookups[col]:
                row[col] = lookups[col][key]
            else:
                drop = True  # parent not in Access yet -> don't write a dangling FK
                break
        if drop:
            skipped += 1
            continue
        resolved.append(row)
    if skipped:
        logger.warning("%s: dropped %d row(s) with an unresolved FK", tm.access_table, skipped)
    return resolved, skipped


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
                    if _is_natural_key_mapping(tm):
                        # Web units matched to EXISTING Access rows by project+block+flat,
                        # then UPDATE-only by that COMPOSITE key (tbl_PropertyDefaults.id
                        # is NOT unique, so it must never be used as the write key). The
                        # resolver detects changes itself and emits only rows to update.
                        writes, where_cols, report = _web_to_access_natural_key(pg_conn, db, tm)
                        tables_processed += 1
                        detail_lines.append(
                            f"{tm.access_table}: resolver "
                            + ", ".join(f"{k}={v}" for k, v in sorted(report.items()))
                        )
                        logger.info("outbound resolver %s: %s", tm.access_table, dict(report))
                        if dry or not writes:
                            continue
                        if not backed_up:
                            path = db.backup(min_interval_minutes=settings.backup_min_interval_minutes,
                                             keep=settings.backup_keep)
                            detail_lines.append(f"backup: {path}" if path
                                                else "backup: skipped (recent backup within interval)")
                            backed_up = True
                        with db.transaction():
                            written = db.update_rows_where(tm.access_table, where_cols, writes)
                        rows_written += written
                        detail_lines.append(f"{tm.access_table}: updated {written} row(s) by {'+'.join(where_cols)}")
                        logger.info("outbound updated %d row(s) in %s", written, tm.access_table)
                        continue

                    incoming, access_cols = _pg_rows_for(pg_conn, tm)
                    if not access_cols:
                        detail_lines.append(f"{tm.access_table}: no active fields, skipped")
                        continue
                    # Resolve any fk: columns (web id -> Access-assigned parent id) now
                    # that the parent owns its key. Parents sync first (mapping order), so
                    # their Access rows exist when the child resolves. Orphans are dropped.
                    incoming, _fk_skipped = _resolve_fk_columns(db, tm, incoming)
                    if _fk_skipped:
                        detail_lines.append(f"{tm.access_table}: skipped {_fk_skipped} row(s) with unresolved FK")
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
                    # Fill cumsum: fields (running totals per group) on the final row set,
                    # after FK resolution so the partition key is the Access parent id.
                    _apply_cumulative(tm, incoming)
                    existing = db.read_rows(tm.access_table, access_cols)
                    diff = compute_diff(tm.access_table, key, existing, incoming)
                    tables_processed += 1
                    detail_lines.append(diff.summary())
                    logger.info("diff %s", diff.summary())

                    if dry or not diff.has_changes:
                        continue

                    if not backed_up:
                        path = db.backup(min_interval_minutes=settings.backup_min_interval_minutes,
                                         keep=settings.backup_keep)
                        detail_lines.append(f"backup: {path}" if path
                                            else "backup: skipped (recent backup within interval)")
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
                            seed = db.reseed_autonumber(tm.access_table, tm.key_column,
                                                        floor=AUTONUMBER_FLOOR)
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
    # Optional per-field value translation (e.g. Access status id -> web status text).
    vmaps: dict[str, dict] = {}
    for f in fields:
        if f.value_map:
            try:
                vmaps[f.crm_field] = json.loads(f.value_map)
            except (ValueError, TypeError):
                logger.warning("bad value_map JSON on %s.%s", tm.access_table, f.access_column)
    access_cols = list(access_to_crm.keys())
    rows = db.read_rows(tm.access_table, access_cols)
    out = []
    for r in rows:
        row = {}
        skip = False
        for c, v in r.items():
            if c not in access_to_crm:
                continue
            cf = access_to_crm[c]
            if cf in vmaps:
                # value_map is a strict whitelist: only listed source values sync;
                # anything else (incl. NULL) leaves the target untouched -- so e.g.
                # only a CANCELLED status flows, never web-owned booked/hold.
                if v is not None and str(v) in vmaps[cf]:
                    v = vmaps[cf][str(v)]
                else:
                    skip = True
                    break
            row[cf] = v
        if not skip:
            out.append(row)
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


def _build_update_sql(target: str, key: str, cols: list[str]) -> str:
    """UPDATE <target> SET <mapped non-key cols> WHERE <key> = %s.

    Inbound is UPDATE-only: we change agreed columns on EXISTING CRM rows and never
    insert. (The CRM's Django tables have many NOT NULL columns -- incl. KYC -- that
    Access does not carry, and Postgres validates them on the proposed INSERT row even
    with ON CONFLICT, so a partial-column upsert fails. A plain UPDATE touches only the
    mapped columns, leaving everything else -- including KYC -- intact.)
    """
    if "." in target:
        schema, name = target.split(".", 1)
        qualified = f'"{schema}"."{name}"'
    else:
        qualified = f'"{target}"'
    set_cols = [c for c in cols if c != key]
    set_clause = ", ".join(f'"{c}" = %s' for c in set_cols)
    return f'UPDATE {qualified} SET {set_clause} WHERE "{key}" = %s'


def _pg_update_rows(conn, target: str, key: str, rows: list[dict]) -> int:
    """UPDATE each existing CRM row (mapped non-key cols only). Returns rows affected.

    Caller manages the transaction. Rows whose key isn't present in the CRM affect 0
    rows (they are reported as skipped by the caller, not inserted).
    """
    affected = 0
    cur = conn.cursor()
    for row in rows:
        cols = list(row.keys())
        set_cols = [c for c in cols if c != key]
        if not set_cols:
            continue  # only the key mapped -> nothing to update
        sql = _build_update_sql(target, key, cols)
        params = [row[c] for c in set_cols] + [row[key]]
        cur.execute(sql, params)
        affected += cur.rowcount
    return affected


# --------------------------------------------------------------------------- #
# access -> web : natural-key booking-status resolver
#
# A booking created directly in Access gets an Access-native PROPERTY_DETAILS_ID
# (AutoNumber) that does NOT correspond to any web unit id, so the plain id-keyed
# inbound path matches nothing. Instead we identify the web unit by its natural key:
#   project (via a crosswalk)  +  block  +  flat_no.
# The office's Access status ids are translated to web status text via the field's
# value_map whitelist (1 -> booked, 3 -> available; everything else is ignored so a
# post-booking lifecycle state can never leak).
# --------------------------------------------------------------------------- #

# Standard control columns on Access tbl_Property_Details, read by the resolver.
_BK_ACTIVE = "ISACTIVE"
_BK_MODIFIED = "MODIFIED_DATE"
_BK_CREATED = "CREATED_DATE"


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "t")
    return bool(v)


def _dt_sort_key(v):
    """Sortable key for a datetime-or-None (None is treated as oldest)."""
    if v is None:
        return float("-inf")
    try:
        return v.timestamp()
    except AttributeError:
        return float("-inf")


def _latest_status(records):
    """records: [(status_value, (modified, created)), ...] for one unit.

    Ranks each record by its *effective* time -- MODIFIED_DATE if the row was ever
    modified, else CREATED_DATE -- and returns the newest record's status. (Using
    modified-then-created as separate keys is wrong: a fresh re-booking has
    modified=None but a recent created, and would lose to an older row that was
    modified-at-cancel. e.g. A-406: rebooked created 20:53 must beat cancelled
    modified 20:34.) If the two newest records tie exactly on effective time but
    disagree on status, returns None -- an unresolvable conflict the caller reports
    and skips rather than guesses.
    """
    best_status = None
    best_key = None
    tie = False
    for status, (modified, created) in records:
        k = _dt_sort_key(modified if modified is not None else created)
        if best_key is None or k > best_key:
            best_status, best_key, tie = status, k, False
        elif k == best_key and status != best_status:
            tie = True
    return None if tie else best_status


def resolve_booking_status(
    rows, *, project_col, block_col, flat_col, status_col,
    status_vmap, crosswalk, web_lookup,
    active_col=None, modified_col=None, created_col=None,
    crm_key="id", status_crm="status",
):
    """Match Access booking rows to web unit ids and decide each unit's status.

    rows        : Access rows as dicts keyed by Access column name.
    crosswalk   : {str(access_project_id): int(web_property_id)}.
    web_lookup  : {(web_property_id:int, block:str, flat:str): [unit_id, ...]}.
    status_vmap : {str(access_status_id): web_status_text} whitelist.

    Returns (incoming, report):
      incoming : [{crm_key: unit_id, status_crm: value}, ...] for units that resolve
                 to a single winning status (ready for the UPDATE-only diff path).
      report   : Counter of outcomes (resolved, skipped_inactive, skipped_status,
                 no_project, no_unit, ambiguous_unit, resolved_conflict, conflict_tie).
    """
    from collections import Counter, defaultdict

    report = Counter()
    per_unit = defaultdict(list)  # unit_id -> [(status_value, (modified, created)), ...]
    for r in rows:
        if active_col is not None:
            a = r.get(active_col)
            if a is not None and not _as_bool(a):
                report["skipped_inactive"] += 1
                continue
        raw = r.get(status_col)
        if raw is None or str(raw) not in status_vmap:
            report["skipped_status"] += 1
            continue
        status_val = status_vmap[str(raw)]
        apid = r.get(project_col)
        wpid = crosswalk.get(str(apid)) if apid is not None else None
        if wpid is None:
            report["no_project"] += 1
            continue
        blk = str(r.get(block_col) or "").strip()
        flat = str(r.get(flat_col) or "").strip()
        hits = web_lookup.get((int(wpid), blk, flat), [])
        if len(hits) == 0:
            report["no_unit"] += 1
            continue
        if len(hits) > 1:
            report["ambiguous_unit"] += 1
            continue
        sort_key = (r.get(modified_col) if modified_col else None,
                    r.get(created_col) if created_col else None)
        per_unit[hits[0]].append((status_val, sort_key))

    incoming = []
    for uid, recs in per_unit.items():
        statuses = {s for s, _ in recs}
        if len(statuses) == 1:
            incoming.append({crm_key: uid, status_crm: recs[0][0]})
            report["resolved"] += 1
        else:
            winner = _latest_status(recs)
            if winner is None:
                report["conflict_tie"] += 1
            else:
                incoming.append({crm_key: uid, status_crm: winner})
                report["resolved_conflict"] += 1
    return incoming, report


def _build_unit_lookup(conn):
    """(web_property_id, block, flat) -> [unit_id, ...] from the sync.unit view.

    unit_id == bookings_unitmaster.id (verified), so matches found here can be
    written straight back to the base table by id.
    """
    from collections import defaultdict
    cur = conn.cursor()
    cur.execute('SELECT property_id, block_name, flat_no, unit_id FROM sync.unit')
    lookup = defaultdict(list)
    for pid, blk, flat, uid in cur.fetchall():
        if pid is None:
            continue
        lookup[(int(pid), str(blk).strip(), str(flat).strip())].append(uid)
    return lookup


def _is_natural_key_mapping(tm) -> bool:
    return any(getattr(f, "role", None) == "match" for f in tm.active_fields)


def _inbound_natural_key(db, pg_conn, tm):
    """Resolve an Access booking mapping (role=match fields) to web unit updates.

    Returns (incoming, crm_key, crm_cols, report) mirroring _access_rows_for so the
    caller can feed it through the same UPDATE-only diff path.
    """
    match_fields = {f.crm_field: f for f in tm.active_fields if f.role == "match"}
    proj_f = match_fields.get("property_id")
    block_f = match_fields.get("block_name")
    flat_f = match_fields.get("flat_no")
    status_f = next(
        (f for f in tm.active_fields
         if f.role == "sync" and not (f.crm_field or "").strip().lower().startswith("const:")),
        None,
    )
    key_f = next((f for f in tm.active_fields if f.role == "key"), None)
    if not (proj_f and block_f and flat_f and status_f and key_f):
        raise EngineError(
            f"{tm.access_table}: natural-key inbound needs role=match fields for "
            "property_id/block_name/flat_no, a role=sync status field, and a role=key field."
        )

    try:
        crosswalk_raw = json.loads(proj_f.value_map or "{}")
    except (ValueError, TypeError):
        raise EngineError(f"{tm.access_table}: bad project crosswalk JSON on {proj_f.access_column}.")
    crosswalk = {str(k): int(v) for k, v in crosswalk_raw.items()}
    try:
        status_vmap = json.loads(status_f.value_map or "{}")
    except (ValueError, TypeError):
        status_vmap = {}

    access_cols = [proj_f.access_column, block_f.access_column, flat_f.access_column,
                   status_f.access_column, _BK_ACTIVE, _BK_MODIFIED, _BK_CREATED]
    rows = db.read_rows(tm.access_table, access_cols)
    web_lookup = _build_unit_lookup(pg_conn)

    incoming, report = resolve_booking_status(
        rows,
        project_col=proj_f.access_column, block_col=block_f.access_column,
        flat_col=flat_f.access_column, status_col=status_f.access_column,
        status_vmap=status_vmap, crosswalk=crosswalk, web_lookup=web_lookup,
        active_col=_BK_ACTIVE, modified_col=_BK_MODIFIED, created_col=_BK_CREATED,
        crm_key=key_f.crm_field, status_crm=status_f.crm_field,
    )
    crm_cols = [key_f.crm_field, status_f.crm_field]
    return incoming, key_f.crm_field, crm_cols, report


# --------------------------------------------------------------------------- #
# web -> access : natural-key UPDATE resolver (for tbl_PropertyDefaults)
#
# The web pushes unit data keyed on UNIT_CODE, but the client's Access
# tbl_PropertyDefaults rows have NO code yet -- so a code-keyed diff sees every web
# unit as new and would INSERT thousands of duplicate rows. Instead we match each web
# unit to its EXISTING Access row by project(+crosswalk)+block+flat and UPDATE it in
# place (filling UNIT_CODE + pricing). Web units with no Access row are reported, not
# inserted. Written by the Access AutoNumber id the match resolves to.
# --------------------------------------------------------------------------- #

def resolve_defaults_updates(
    web_rows, access_rows, *, project_crm, block_crm, flat_crm, set_map, crosswalk,
    key_access_cols,
):
    """Match web unit rows to existing Access rows by project+block+flat and build
    COMPOSITE-key UPDATE rows -- only for units whose mapped values actually change.

    web_rows        : dicts keyed by crm_field (property_id, block_name, flat_no, + set cols).
    access_rows     : dicts keyed by Access column (key_access_cols + set targets).
    crosswalk       : {str(web_property_id): access_project_value}.
    set_map         : {crm_field: access_column} for the columns to write.
    key_access_cols : [project_col, block_col, flat_col] Access columns forming the key.

    Each returned write = {**access_key_values, **changed_set_values}, ready for
    update_rows_where. The key values are taken from the matched ACCESS row (so the
    WHERE clause matches exactly). Returns (writes, report).

    Note: the Access key columns are compared as stripped strings, so the write key
    is the composite (project, block, flat) -- NOT the ``id`` column, which is not
    unique in tbl_PropertyDefaults and must never be used as a write key.
    """
    from collections import Counter, defaultdict
    from agent.diff import _values_equal

    proj_col, block_col, flat_col = key_access_cols
    by_key = defaultdict(list)
    for a in access_rows:
        k = (str(a.get(proj_col)).strip(), str(a.get(block_col)).strip(), str(a.get(flat_col)).strip())
        by_key[k].append(a)

    report = Counter()
    writes = []
    for r in web_rows:
        wpid = r.get(project_crm)
        ap = crosswalk.get(str(wpid)) if wpid is not None else None
        if ap is None:
            report["no_project"] += 1
            continue
        k = (str(ap).strip(), str(r.get(block_crm) or "").strip(), str(r.get(flat_crm) or "").strip())
        matches = by_key.get(k, [])
        if len(matches) == 0:
            report["no_row"] += 1
            continue
        if len(matches) > 1:
            # Composite key not unique on the Access side -> can't target one row.
            report["ambiguous"] += 1
            continue
        acc = matches[0]
        changed = {}
        for crm, acol in set_map.items():
            new_val = r.get(crm)
            if not _values_equal(acc.get(acol), new_val):
                changed[acol] = new_val
        if not changed:
            report["unchanged"] += 1
            continue
        where = {col: acc.get(col) for col in key_access_cols}
        writes.append({**where, **changed})
        report["updated"] += 1
    return writes, report


def _web_to_access_natural_key(pg_conn, db, tm):
    """Resolve web unit rows to existing Access rows (project+block+flat) and produce
    COMPOSITE-key UPDATE rows. Returns (writes, key_access_cols, report)."""
    match_fields = {f.crm_field: f for f in tm.active_fields if f.role == "match"}
    proj_f = match_fields.get("property_id")
    block_f = match_fields.get("block_name")
    flat_f = match_fields.get("flat_no")
    set_fields = [f for f in tm.active_fields
                  if f.role == "sync"
                  and not (f.crm_field or "").strip().lower().startswith("const:")]
    if not (proj_f and block_f and flat_f and set_fields):
        raise EngineError(
            f"{tm.access_table}: natural-key outbound needs role=match fields for "
            "property_id/block_name/flat_no and at least one role=sync field."
        )
    try:
        crosswalk = {str(k): str(v) for k, v in json.loads(proj_f.value_map or "{}").items()}
    except (ValueError, TypeError):
        raise EngineError(f"{tm.access_table}: bad project crosswalk JSON on {proj_f.access_column}.")

    key_access_cols = [proj_f.access_column, block_f.access_column, flat_f.access_column]
    set_map = {f.crm_field: f.access_column for f in set_fields}

    # web source rows
    crm_cols = [proj_f.crm_field, block_f.crm_field, flat_f.crm_field] + [f.crm_field for f in set_fields]
    source = tm.pg_table or tm.access_table
    if "." in source:
        schema, name = source.split(".", 1)
        qualified = f'"{schema}"."{name}"'
    else:
        qualified = f'"{source}"'
    cur = pg_conn.cursor()
    cur.execute("SELECT " + ", ".join(f'"{c}"' for c in crm_cols) + f" FROM {qualified}")
    names = [d[0] for d in cur.description]
    web_rows = [dict(zip(names, r)) for r in cur.fetchall()]

    # current Access rows: key columns + the set targets (for change detection)
    access_rows = db.read_rows(tm.access_table, key_access_cols + list(set_map.values()))

    writes, report = resolve_defaults_updates(
        web_rows, access_rows, project_crm=proj_f.crm_field, block_crm=block_f.crm_field,
        flat_crm=flat_f.crm_field, set_map=set_map, crosswalk=crosswalk,
        key_access_cols=key_access_cols,
    )
    return writes, key_access_cols, report


def run_access_to_web(trigger: str = "manual", dry_run: bool | None = None) -> SyncRun:
    """Push agreed Access changes back to the CRM (access -> web).

    Mirrors run_web_to_access with the direction reversed: Access is the source,
    the CRM table is the target, and Access wins. UPDATE-only: agreed columns on
    existing CRM rows are updated; new Access rows are skipped (a CRM insert needs
    required/KYC fields Access doesn't carry). Writes are transactional per table.
    Requires UPDATE access on the CRM base table; until granted the live path errors
    per table, which is caught and reported. Defaults to whatever settings.dry_run says.
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
                    if _is_natural_key_mapping(tm):
                        # Booking rows born in Access: match to web units by
                        # project(+crosswalk)+block+flat, not by id.
                        incoming, crm_key, crm_cols, report = _inbound_natural_key(db, pg_conn, tm)
                        detail_lines.append(
                            f"{tm.access_table}: resolver "
                            + ", ".join(f"{k}={v}" for k, v in sorted(report.items()))
                        )
                        logger.info("inbound resolver %s: %s", tm.access_table, dict(report))
                    else:
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

                    # Inbound is UPDATE-only: change agreed columns on existing CRM
                    # rows. New Access rows (diff.inserts) are skipped, not inserted --
                    # creating a CRM record needs required/KYC fields Access lacks.
                    updated = _pg_update_rows(pg_conn, target, crm_key,
                                              [c.new_row for c in diff.updates])
                    pg_conn.commit()
                    rows_written += updated
                    skipped_new = len(diff.inserts)
                    note = f"{target}: updated {updated} existing row(s)"
                    if skipped_new:
                        note += f"; skipped {skipped_new} new row(s) (inbound is update-only)"
                    detail_lines.append(note)
                    logger.info("inbound updated %d row(s) in %s (skipped %d new)",
                                updated, target, skipped_new)
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
