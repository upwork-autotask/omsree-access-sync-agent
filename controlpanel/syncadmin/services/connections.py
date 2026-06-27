"""Live connection tests and schema introspection for both databases.

Access: reuses agent.access_db.PyodbcAccessDatabase.
PostgreSQL: psycopg2 against the OmSree CRM DB.

Every function returns plain data (lists / (ok, message) tuples) so views and the
mapping dropdowns can use them without caring about driver details.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TestResult:
    ok: bool
    message: str


# --------------------------------------------------------------------------- #
# MS Access
# --------------------------------------------------------------------------- #
def _open_access(path: str, password: str):
    from agent.access_db import PyodbcAccessDatabase

    return PyodbcAccessDatabase(path, password or "")


def test_access(path: str, password: str) -> TestResult:
    if not path:
        return TestResult(False, "No Access DB path set.")
    try:
        db = _open_access(path, password)
        try:
            n = len(db.list_tables())
        finally:
            db.close()
        return TestResult(True, f"Connected. {n} user table(s) found.")
    except Exception as exc:
        return TestResult(False, str(exc))


def access_tables(path: str, password: str) -> list[str]:
    db = _open_access(path, password)
    try:
        return db.list_tables()
    finally:
        db.close()


def access_columns(path: str, password: str, table: str) -> list[str]:
    db = _open_access(path, password)
    try:
        return [c["name"] for c in db.table_columns(table)]
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# PostgreSQL (OmSree CRM)
# --------------------------------------------------------------------------- #
def _pg_connect(host, port, dbname, user, password, sslmode="prefer", timeout=8):
    import psycopg2

    return psycopg2.connect(
        host=host or "localhost",
        port=int(port or 5432),
        dbname=dbname,
        user=user,
        password=password or "",
        sslmode=sslmode or "prefer",
        connect_timeout=timeout,
    )


def test_postgres(host, port, dbname, user, password, sslmode="prefer") -> TestResult:
    if not (host and dbname and user):
        return TestResult(False, "Host, database and user are required.")
    try:
        conn = _pg_connect(host, port, dbname, user, password, sslmode)
        try:
            cur = conn.cursor()
            cur.execute("SELECT version()")
            ver = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog','information_schema')"
            )
            ntables = cur.fetchone()[0]
        finally:
            conn.close()
        short = ver.split(",")[0] if ver else "PostgreSQL"
        return TestResult(True, f"Connected to {short}. {ntables} table(s) visible.")
    except Exception as exc:
        return TestResult(False, str(exc))


def pg_tables(host, port, dbname, user, password, sslmode="prefer") -> list[str]:
    conn = _pg_connect(host, port, dbname, user, password, sslmode)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
            "AND table_type IN ('BASE TABLE','VIEW') ORDER BY table_schema, table_name"
        )
        out = []
        for schema, name in cur.fetchall():
            out.append(name if schema == "public" else f"{schema}.{name}")
        return out
    finally:
        conn.close()


def pg_columns(host, port, dbname, user, password, table, sslmode="prefer") -> list[str]:
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = "public", table
    conn = _pg_connect(host, port, dbname, user, password, sslmode)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            [schema, name],
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
