import sys

from django.apps import AppConfig
from django.conf import settings


class SyncadminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "syncadmin"
    verbose_name = "OmSree Sync Agent"

    def ready(self):
        # Only start the cron scheduler when actually serving — never during
        # migrate/makemigrations/test/shell, which would race the DB.
        if not getattr(settings, "SCHEDULER_AUTOSTART", False):
            return
        serving = any(cmd in sys.argv for cmd in ("runserver",)) or "waitress" in " ".join(sys.argv)
        # waitress-serve imports wsgi without django argv; detect by absence of management cmds.
        management_cmds = {
            "migrate", "makemigrations", "collectstatic", "shell", "test",
            "createsuperuser", "seed_admin", "import_mappings", "export_mappings",
            "loaddata", "dumpdata", "check", "sync_now",
        }
        if any(c in sys.argv for c in management_cmds):
            return

        # Start just off the init thread: reading settings during AppConfig.ready()
        # triggers Django's "DB access during app init" warning. A short timer runs
        # it once the app is fully ready.
        import threading

        def _start():
            try:
                from .services.scheduler import start_scheduler
                start_scheduler()
            except Exception as exc:  # never let scheduler startup crash the app
                print(f"[scheduler] not started: {exc}", file=sys.stderr)

        threading.Timer(1.5, _start).start()
