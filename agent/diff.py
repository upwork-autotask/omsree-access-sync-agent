"""Pure diff engine: compare web-origin incoming rows against current Access rows.

This module is deliberately free of I/O so the most important logic in the agent
(what *would* change) is trivially testable. Conflict rule for web-origin tables
is "web always wins" (docs/implementation-plan.md s.4), so the diff simply asks:
which incoming rows are new, and which differ on any synced column?
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Sequence


Row = Mapping[str, Any]


@dataclass
class FieldChange:
    column: str
    old: Any
    new: Any


@dataclass
class RowChange:
    key_value: Any
    kind: str  # "insert" | "update"
    new_row: dict[str, Any]
    field_changes: list[FieldChange] = field(default_factory=list)


@dataclass
class TableDiff:
    table: str
    key: str
    inserts: list[RowChange] = field(default_factory=list)
    updates: list[RowChange] = field(default_factory=list)
    unchanged: int = 0
    skipped_no_key: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.inserts or self.updates)

    @property
    def change_count(self) -> int:
        return len(self.inserts) + len(self.updates)

    def summary(self) -> str:
        return (
            f"{self.table}: {len(self.inserts)} insert(s), "
            f"{len(self.updates)} update(s), {self.unchanged} unchanged"
            + (f", {self.skipped_no_key} skipped(no key)" if self.skipped_no_key else "")
        )


def _normalize(value: Any) -> Any:
    """Normalize a single value (strings stripped). Kept for callers/tests."""
    if isinstance(value, str):
        return value.strip()
    return value


def _as_number(v: Any):
    """Return a float for numeric values, else None. Bools are NOT numbers here."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        try:
            return float(v)
        except (ValueError, OverflowError):
            return None
    return None


def _as_datetime_key(v: Any):
    """Normalize date/datetime to a (y,m,d,h,m,s) tuple, dropping tz + microseconds.

    Access stores datetimes to second precision with no timezone, while the web
    sends tz-aware datetimes with microseconds (and sometimes plain dates). Compare
    them on wall-clock seconds so equal instants don't look like changes.
    """
    if isinstance(v, _dt.datetime):
        return (v.year, v.month, v.day, v.hour, v.minute, v.second)
    if isinstance(v, _dt.date):
        return (v.year, v.month, v.day, 0, 0, 0)
    return None


def _values_equal(old: Any, new: Any) -> bool:
    """Semantic equality so cosmetic/type differences don't show as changes.

    Handles: string whitespace, NULL vs empty-string, numeric type/precision
    (6.9 == 6.90, float vs Decimal), date vs datetime and sub-second/tz precision,
    and boolean NULL vs False (an Access BIT column stores web None as False).
    """
    a, b = _normalize(old), _normalize(new)
    if a == b:
        return True
    # NULL and empty string are both "no value"
    if (a is None and b == "") or (b is None and a == ""):
        return True
    # boolean vs None: a web None written into an Access BIT becomes False
    if isinstance(a, bool) or isinstance(b, bool):
        av = a if isinstance(a, bool) else (False if a is None else a)
        bv = b if isinstance(b, bool) else (False if b is None else b)
        return av == bv
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        return round(na, 6) == round(nb, 6)
    da, db = _as_datetime_key(a), _as_datetime_key(b)
    if da is not None and db is not None:
        return da == db
    return False


def compute_diff(
    table: str,
    key: str,
    existing_rows: Sequence[Row],
    incoming_rows: Sequence[Row],
) -> TableDiff:
    """Compute what incoming (web) rows would change in existing (Access) rows.

    Only columns present on an incoming row are considered. Columns that exist in
    Access but not in the incoming payload are left untouched (never overwritten),
    matching the "unknown/extra columns are ignored" rule.
    """
    existing_by_key: dict[Any, Row] = {}
    for row in existing_rows:
        if key in row and row[key] is not None:
            existing_by_key[row[key]] = row

    diff = TableDiff(table=table, key=key)

    for incoming in incoming_rows:
        if key not in incoming or incoming[key] is None:
            diff.skipped_no_key += 1
            continue

        key_value = incoming[key]
        new_row = dict(incoming)
        current = existing_by_key.get(key_value)

        if current is None:
            diff.inserts.append(RowChange(key_value=key_value, kind="insert", new_row=new_row))
            continue

        field_changes: list[FieldChange] = []
        for column, new_value in incoming.items():
            if column == key:
                continue
            old_value = current.get(column)
            if not _values_equal(old_value, new_value):
                field_changes.append(FieldChange(column=column, old=old_value, new=new_value))

        if field_changes:
            diff.updates.append(
                RowChange(
                    key_value=key_value,
                    kind="update",
                    new_row=new_row,
                    field_changes=field_changes,
                )
            )
        else:
            diff.unchanged += 1

    return diff


def format_diff(diff: TableDiff, *, max_rows: int = 20) -> str:
    """Human-readable diff for --dry-run output."""
    lines = [diff.summary()]
    shown = 0
    for change in diff.inserts:
        if shown >= max_rows:
            break
        lines.append(f"  + [{diff.key}={change.key_value}] insert")
        shown += 1
    for change in diff.updates:
        if shown >= max_rows:
            break
        parts = ", ".join(f"{fc.column}: {fc.old!r} -> {fc.new!r}" for fc in change.field_changes)
        lines.append(f"  ~ [{diff.key}={change.key_value}] {parts}")
        shown += 1
    remaining = diff.change_count - shown
    if remaining > 0:
        lines.append(f"  ... and {remaining} more change(s)")
    return "\n".join(lines)
