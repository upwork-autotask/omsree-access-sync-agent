"""Command-line entry point for the sync agent.

  python -m agent sync --dry-run     # read Access + pull outbound, print a diff, write nothing
  python -m agent sync               # apply changes inside a transaction, then ack
  python -m agent sync --loop        # run forever on SYNC_INTERVAL_MINUTES
  python -m agent status             # last sync time, cursor, last error
"""

from __future__ import annotations

import argparse
import sys
import time

from .access_db import AccessError, InMemoryAccessDatabase, PyodbcAccessDatabase
from .config import Config, ConfigError
from .crm_client import CrmClient, CrmError
from .state import SyncState, setup_logging
from .sync import Syncer


def _build_db(config, logger, *, dry_run: bool):
    """Open the real Access DB. In dry-run, fall back to an empty in-memory DB
    if the driver/file is unavailable so the diff can still be computed."""
    try:
        return PyodbcAccessDatabase(config.access_db_path, config.access_db_password)
    except AccessError as exc:
        if dry_run:
            logger.warning("%s", exc)
            logger.warning("dry-run: using empty in-memory Access DB; all rows will show as inserts")
            return InMemoryAccessDatabase()
        raise


def cmd_sync(args, config: Config) -> int:
    logger = setup_logging(config.log_file, verbose=args.verbose)
    dry_run = True if args.dry_run else config.dry_run
    state = SyncState.load(config.state_file)

    def one_pass() -> int:
        db = _build_db(config, logger, dry_run=dry_run)
        try:
            crm = CrmClient(config.crm_base_url, config.agent_token)
            syncer = Syncer(config, crm, db, state)
            syncer.run_once(dry_run=dry_run)
        except (CrmError, AccessError) as exc:
            logger.error("sync failed: %s", exc)
            state.last_result = "error"
            state.last_error = str(exc)
            state.save(config.state_file)
            return 1
        finally:
            db.close()
        state.save(config.state_file)
        return 0

    if not args.loop:
        return one_pass()

    interval = max(1, config.sync_interval_minutes) * 60
    logger.info("loop mode: every %d minute(s). Ctrl-C to stop.", config.sync_interval_minutes)
    while True:
        one_pass()  # keep looping even if a single pass errors
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("stopped.")
            return 0


def cmd_schema(args, config: Config) -> int:
    """Introspect the Access DB: list tables, or dump one table's columns."""
    logger = setup_logging(config.log_file, verbose=args.verbose)
    try:
        db = PyodbcAccessDatabase(config.access_db_path, config.access_db_password)
    except AccessError as exc:
        logger.error("%s", exc)
        return 1
    try:
        if args.table:
            print(f"{args.table}  (rows={db.row_count(args.table)})")
            for col in db.table_columns(args.table):
                nn = "" if col["nullable"] else "  NOT NULL"
                print(f"  - {col['name']}  [{col['type']} {col['size']}]{nn}")
        else:
            for name in db.list_tables():
                marker = " *" if config.table_whitelist and name in config.table_whitelist else ""
                print(f"{name}  (rows={db.row_count(name)}){marker}")
    finally:
        db.close()
    return 0


def cmd_status(args, config: Config) -> int:
    state = SyncState.load(config.state_file)
    print(f"Access DB    : {config.access_db_path}")
    print(f"CRM          : {config.crm_base_url}")
    print(f"Whitelist    : {', '.join(config.table_whitelist) or 'ALL'}")
    print(f"Dry-run dflt : {config.dry_run}")
    print(f"State file   : {config.state_file}")
    print("-" * 40)
    print(f"Last sync    : {state.last_sync or 'never'}")
    print(f"Last result  : {state.last_result or '-'}")
    print(f"Cursor       : {state.cursor or '-'}")
    print(f"Rows written : {state.rows_written}")
    if state.last_error:
        print(f"Last error   : {state.last_error}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent", description="OmSree MS Access <-> CRM sync agent")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="pull web-origin changes and apply to Access")
    p_sync.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    p_sync.add_argument("--loop", action="store_true", help="run continuously on the configured interval")
    p_sync.set_defaults(func=cmd_sync)

    p_schema = sub.add_parser("schema", help="introspect the Access DB (tables / columns)")
    p_schema.add_argument("table", nargs="?", help="dump this table's columns; omit to list all tables")
    p_schema.set_defaults(func=cmd_schema)

    p_status = sub.add_parser("status", help="show last sync time, cursor, last error")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
