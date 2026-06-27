"""Seed the mapping tables from the CSVs in docs/ (idempotent)."""

from django.core.management.base import BaseCommand

from syncadmin.services import mappings


class Command(BaseCommand):
    help = "Import docs/sync-field-mapping*.csv into the mapping tables."

    def handle(self, *args, **opts):
        res = mappings.seed_from_docs()
        self.stdout.write(self.style.SUCCESS(
            f"Seeded: {res['tables']} new table mapping(s), {res['fields']} field(s)."
        ))
