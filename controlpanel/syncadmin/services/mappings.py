"""CSV import/export and seeding of the mapping tables.

The CSV shape matches docs/sync-field-mapping*.csv:
  access_table, web_entity, access_column, crm_field, type,
  direction, decision, conflict_rule, cursor_column, notes
('decision' maps to FieldMapping.role; direction text 'web->access' -> 'web2access'.)
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from django.conf import settings as dj_settings

from ..models import FieldMapping, TableMapping

DIR_MAP = {
    "web->access": TableMapping.WEB2ACCESS,
    "web2access": TableMapping.WEB2ACCESS,
    "access->web": TableMapping.ACCESS2WEB,
    "access2web": TableMapping.ACCESS2WEB,
}
HEADER = ["access_table", "web_entity", "access_column", "crm_field", "type",
          "direction", "decision", "conflict_rule", "cursor_column", "notes"]


def import_rows(rows) -> dict:
    """Upsert TableMapping/FieldMapping from an iterable of dict rows."""
    tables = 0
    fields = 0
    seen_tables = set()
    for r in rows:
        access_table = (r.get("access_table") or "").strip()
        access_column = (r.get("access_column") or "").strip()
        if not access_table or not access_column:
            continue
        direction = DIR_MAP.get((r.get("direction") or "").strip(), TableMapping.WEB2ACCESS)
        role = (r.get("decision") or r.get("role") or "sync").strip()
        is_key = role == "key"

        tm, created = TableMapping.objects.get_or_create(
            direction=direction, access_table=access_table,
            defaults={
                "web_entity": (r.get("web_entity") or "").strip(),
                "conflict_rule": (r.get("conflict_rule") or "web-wins").strip(),
                "cursor_column": (r.get("cursor_column") or "").strip(),
                "key_column": access_column if is_key else "",
            },
        )
        if created:
            tables += 1
        if is_key and not tm.key_column:
            tm.key_column = access_column
            tm.save(update_fields=["key_column"])
        if access_table not in seen_tables and not tm.pg_table:
            tm.pg_table = access_table  # sensible default; editable in UI
            tm.save(update_fields=["pg_table"])
            seen_tables.add(access_table)

        FieldMapping.objects.update_or_create(
            table=tm, access_column=access_column,
            defaults={
                "crm_field": (r.get("crm_field") or access_column).strip(),
                "data_type": (r.get("type") or "").strip(),
                "role": role if role in dict(FieldMapping.ROLES) else "sync",
                # By default only key + 'sync'/'yes' fields are active.
                "is_active": role in ("key", "sync", "yes"),
                "notes": (r.get("notes") or "").strip(),
            },
        )
        fields += 1
    return {"tables": tables, "fields": fields}


def import_csv_text(text: str) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    missing = [h for h in ("access_table", "access_column") if h not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"CSV missing required column(s): {', '.join(missing)}")
    return import_rows(reader)


def export_csv_text(direction: str | None = None) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=HEADER)
    w.writeheader()
    qs = TableMapping.objects.all()
    if direction:
        qs = qs.filter(direction=direction)
    for tm in qs:
        dir_text = "web->access" if tm.direction == TableMapping.WEB2ACCESS else "access->web"
        for f in tm.fields.all():
            w.writerow({
                "access_table": tm.access_table,
                "web_entity": tm.web_entity,
                "access_column": f.access_column,
                "crm_field": f.crm_field,
                "type": f.data_type,
                "direction": dir_text,
                "decision": f.role,
                "conflict_rule": tm.conflict_rule,
                "cursor_column": tm.cursor_column,
                "notes": f.notes,
            })
    return out.getvalue()


def seed_from_docs() -> dict:
    """Import the two CSVs shipped in docs/ (idempotent)."""
    docs = Path(dj_settings.REPO_ROOT) / "docs"
    total = {"tables": 0, "fields": 0}
    for name in ("sync-field-mapping.csv", "sync-field-mapping-inbound.csv"):
        path = docs / name
        if path.exists():
            res = import_csv_text(path.read_text(encoding="utf-8-sig"))
            total["tables"] += res["tables"]
            total["fields"] += res["fields"]
    return total
