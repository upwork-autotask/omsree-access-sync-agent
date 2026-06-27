"""Access database layer.

Defines the small interface the syncer needs, plus two implementations:

  * PyodbcAccessDatabase - talks to a real .accdb/.mdb via the ACE ODBC driver.
  * InMemoryAccessDatabase - a dependency-free fake used by tests and by
    `--dry-run` when no driver is present, so the diff path always works.

Only the agreed (whitelisted) columns are ever written. Rows are upserted by
key; the agent never deletes rows (docs/implementation-plan.md s.4).
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


class AccessError(Exception):
    """Raised on any Access/ODBC failure."""


class AccessDatabase(ABC):
    """Minimal surface the syncer depends on."""

    @abstractmethod
    def read_rows(self, table: str, columns: Sequence[str]) -> list[dict[str, Any]]:
        """Return current rows for the given columns (used to compute the diff)."""

    @abstractmethod
    def upsert_rows(self, table: str, key: str, rows: Sequence[dict[str, Any]]) -> int:
        """Insert or update rows by key. Returns the number of rows written."""

    @abstractmethod
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Context manager: commit on success, roll back on exception."""

    def backup(self) -> Path | None:
        """Take a safety copy before the first write of a run. Default: no-op."""
        return None

    def list_tables(self) -> list[str]:
        """Return user table names (excluding system/hidden tables)."""
        raise NotImplementedError

    def table_columns(self, table: str) -> list[dict[str, Any]]:
        """Return column metadata (name/type/size/nullable) for a table."""
        raise NotImplementedError

    def row_count(self, table: str) -> int:
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - trivial default
        pass


class InMemoryAccessDatabase(AccessDatabase):
    """A fake Access DB backed by Python dicts. No external dependencies."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        # table -> list of row dicts
        self.tables: dict[str, list[dict[str, Any]]] = tables or {}
        self.committed = 0
        self.rolled_back = 0
        self._snapshot: dict[str, list[dict[str, Any]]] | None = None

    def read_rows(self, table: str, columns: Sequence[str]) -> list[dict[str, Any]]:
        rows = self.tables.get(table, [])
        return [{c: row.get(c) for c in columns if c in row} for row in rows]

    def list_tables(self) -> list[str]:
        return sorted(self.tables)

    def table_columns(self, table: str) -> list[dict[str, Any]]:
        cols: list[str] = []
        for row in self.tables.get(table, []):
            for c in row:
                if c not in cols:
                    cols.append(c)
        return [{"name": c, "type": "unknown", "size": None, "nullable": True} for c in cols]

    def row_count(self, table: str) -> int:
        return len(self.tables.get(table, []))

    def upsert_rows(self, table: str, key: str, rows: Sequence[dict[str, Any]]) -> int:
        existing = self.tables.setdefault(table, [])
        by_key = {r.get(key): r for r in existing}
        written = 0
        for incoming in rows:
            kv = incoming.get(key)
            if kv in by_key:
                by_key[kv].update(incoming)
            else:
                new_row = dict(incoming)
                existing.append(new_row)
                by_key[kv] = new_row
            written += 1
        return written

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._snapshot = {t: [dict(r) for r in rows] for t, rows in self.tables.items()}
        try:
            yield
        except Exception:
            assert self._snapshot is not None
            self.tables = self._snapshot
            self.rolled_back += 1
            raise
        else:
            self.committed += 1
        finally:
            self._snapshot = None


class PyodbcAccessDatabase(AccessDatabase):
    """Real Access DB via the Microsoft Access ODBC driver (Windows)."""

    DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"

    def __init__(self, db_path: str | Path, password: str = "") -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise AccessError(f"Access DB not found: {self.db_path}")
        try:
            import pyodbc
        except ImportError as exc:  # pragma: no cover - import guard
            raise AccessError(
                "pyodbc is required to talk to a real Access DB; pip install -r requirements.txt"
            ) from exc
        self._pyodbc = pyodbc
        conn_str = f"DRIVER={self.DRIVER};DBQ={self.db_path};"
        if password:
            conn_str += f"PWD={password};"
        try:
            self._conn = pyodbc.connect(conn_str, autocommit=False)
        except Exception as exc:  # pragma: no cover - needs a driver to hit
            raise AccessError(
                f"could not open {self.db_path}: {exc}. Check the password and that the "
                f"Access ODBC driver is installed (64-bit Python needs the 64-bit ACE driver)."
            ) from exc
        # The ACE driver returns metadata in a way that trips pyodbc's default
        # unicode handling; latin-1 round-trips the bytes safely.
        self._conn.setdecoding(pyodbc.SQL_CHAR, encoding="latin-1")
        self._conn.setdecoding(pyodbc.SQL_WCHAR, encoding="latin-1")
        self._conn.setencoding(encoding="latin-1")

    @staticmethod
    def _quote(identifier: str) -> str:
        # Access quotes identifiers with square brackets.
        return "[" + identifier.replace("]", "]]") + "]"

    def list_tables(self) -> list[str]:
        cur = self._conn.cursor()
        names: list[str] = []
        for row in cur.tables(tableType="TABLE"):
            name = row.table_name
            if name.startswith("MSys") or name.startswith("~"):
                continue
            names.append(name)
        return sorted(names)

    def table_columns(self, table: str) -> list[dict[str, Any]]:
        # Use a zero-row SELECT and read cursor.description; cur.columns() trips
        # the ACE driver's metadata unicode bug on some columns.
        cur = self._conn.cursor()
        try:
            cur.execute(f"SELECT * FROM {self._quote(table)} WHERE 1=0")
        except Exception as exc:  # pragma: no cover - needs a real DB
            raise AccessError(f"describe failed on {table}: {exc}") from exc
        out = []
        for d in cur.description:
            out.append({"name": d[0], "type": d[1].__name__, "size": d[3], "nullable": bool(d[6])})
        return out

    def row_count(self, table: str) -> int:
        cur = self._conn.cursor()
        return cur.execute(f"SELECT COUNT(*) FROM {self._quote(table)}").fetchone()[0]

    def read_rows(self, table: str, columns: Sequence[str]) -> list[dict[str, Any]]:
        col_sql = ", ".join(self._quote(c) for c in columns)
        sql = f"SELECT {col_sql} FROM {self._quote(table)}"
        try:
            cur = self._conn.cursor()
            cur.execute(sql)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]
        except Exception as exc:  # pragma: no cover - needs a real DB
            raise AccessError(f"read failed on {table}: {exc}") from exc

    def upsert_rows(self, table: str, key: str, rows: Sequence[dict[str, Any]]) -> int:
        written = 0
        cur = self._conn.cursor()
        for row in rows:
            columns = list(row.keys())
            if key not in columns:  # pragma: no cover - guarded upstream
                raise AccessError(f"row missing key {key!r} for table {table}")
            non_key = [c for c in columns if c != key]
            placeholders = ", ".join(["?"] * len(columns))
            col_sql = ", ".join(self._quote(c) for c in columns)
            update_existing = (
                f"UPDATE {self._quote(table)} SET "
                + ", ".join(f"{self._quote(c)} = ?" for c in non_key)
                + f" WHERE {self._quote(key)} = ?"
            )
            insert_new = (
                f"INSERT INTO {self._quote(table)} ({col_sql}) VALUES ({placeholders})"
            )
            try:
                if non_key:
                    cur.execute(update_existing, [row[c] for c in non_key] + [row[key]])
                    if cur.rowcount == 0:
                        cur.execute(insert_new, [row[c] for c in columns])
                else:
                    # key-only row: insert if absent
                    exists = cur.execute(
                        f"SELECT 1 FROM {self._quote(table)} WHERE {self._quote(key)} = ?",
                        [row[key]],
                    ).fetchone()
                    if not exists:
                        cur.execute(insert_new, [row[c] for c in columns])
            except Exception as exc:  # pragma: no cover - needs a real DB
                raise AccessError(f"write failed on {table} {key}={row.get(key)}: {exc}") from exc
            written += 1
        return written

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def backup(self) -> Path | None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = self.db_path.with_name(f"{self.db_path.stem}.backup-{stamp}{self.db_path.suffix}")
        try:
            shutil.copy2(self.db_path, backup_path)
        except Exception as exc:  # pragma: no cover - filesystem dependent
            raise AccessError(f"backup failed: {exc}") from exc
        return backup_path

    def close(self) -> None:  # pragma: no cover - needs a real connection
        try:
            self._conn.close()
        except Exception:
            pass


def open_access_db(db_path: str | Path, password: str = "") -> AccessDatabase:
    """Open a real Access DB via the ODBC driver."""
    return PyodbcAccessDatabase(db_path, password)
