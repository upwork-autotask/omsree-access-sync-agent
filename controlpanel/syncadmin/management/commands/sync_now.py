"""Headless one-pass sync (for cron/Task Scheduler if ever wanted)."""

from django.core.management.base import BaseCommand

from syncadmin.services import engine


class Command(BaseCommand):
    help = "Run one web->access sync pass and exit."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        run = engine.run_web_to_access(trigger="scheduled", dry_run=opts["dry_run"] or None)
        self.stdout.write(self.style.SUCCESS(
            f"status={run.status} rows={run.rows_written} tables={run.tables_processed}"
        ) if run.status != "error" else self.style.ERROR(f"error: {run.error_message}"))
