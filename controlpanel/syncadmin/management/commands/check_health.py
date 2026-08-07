"""Watchdog: email an alert if the sync has FAILED or gone STALE.

Schedule this INDEPENDENTLY of the sync agent (its own Task Scheduler entry, e.g.
hourly) so it also catches the agent stopping entirely - the agent can't report
its own death, but this separate check can.

    python manage.py check_health
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from syncadmin.models import AgentSettings, SyncRun
from syncadmin.services import health, mailer


class Command(BaseCommand):
    help = "Check sync health; email an alert if the last sync failed or is stale."

    def handle(self, *args, **opts):
        s = AgentSettings.get_solo()
        last_run = SyncRun.objects.order_by("-id").first()
        last_ok = SyncRun.objects.filter(status__in=["ok", "dry-run"]).order_by("-id").first()
        ok, reason = health.verdict(last_run, last_ok, timezone.now(), s.health_stale_minutes)
        if ok:
            self.stdout.write(self.style.SUCCESS(f"healthy: {reason}"))
            return
        self.stdout.write(self.style.ERROR(f"UNHEALTHY: {reason}"))
        if not (s.alerts_enabled and s.recipient_list):
            self.stdout.write("  (email alert NOT sent - alerts disabled or no recipients; "
                              "configure SMTP + recipients + enable alerts)")
            return
        mailer.send_alert(
            s, "OmSree Sync Agent - ALERT: sync not healthy",
            f"The OmSree sync agent is not healthy.\n\n{reason}\n\n"
            f"Open the control panel to investigate: http://127.0.0.1:8787\n"
            f"(This is a throttled watchdog alert.)")
        self.stdout.write("  alert email attempted (throttled).")
