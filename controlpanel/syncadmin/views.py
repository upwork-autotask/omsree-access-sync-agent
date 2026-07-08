"""All control-panel views. Every view requires a logged-in staff user."""

from __future__ import annotations

import threading

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import AgentSettings, FieldMapping, SyncRun, TableMapping
from .services import connections as conn_svc, engine, mailer, mappings as mapping_svc, scheduler


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@staff_member_required
def dashboard(request):
    s = AgentSettings.get_solo()
    last = SyncRun.objects.first()
    ctx = {
        "s": s,
        "last": last,
        "next_run": scheduler.next_run_time(),
        "active_w2a": TableMapping.objects.filter(direction="web2access", is_active=True).count(),
        "active_a2w": TableMapping.objects.filter(direction="access2web", is_active=True).count(),
    }
    return render(request, "syncadmin/dashboard.html", ctx)


@staff_member_required
@require_POST
def sync_now(request):
    dry = request.POST.get("dry_run") == "1"
    direction = request.POST.get("direction", "web2access")
    if direction == "access2web":
        target, label = engine.run_access_to_web, "Access -> web"
    else:
        target, label = engine.run_web_to_access, "Web -> Access"
    # Run off the request thread so the page returns immediately.
    threading.Thread(
        target=target,
        kwargs={"trigger": "manual", "dry_run": dry},
        daemon=True,
    ).start()
    messages.success(request, f"{label} sync started ({'dry-run' if dry else 'live'}). Check Logs for the result.")
    return redirect("dashboard")


@staff_member_required
@require_POST
def toggle_direction(request, direction):
    s = AgentSettings.get_solo()
    if direction == "web2access":
        s.web_to_access_enabled = not s.web_to_access_enabled
    elif direction == "access2web":
        s.access_to_web_enabled = not s.access_to_web_enabled
    s.save()
    messages.success(request, "Updated syncing direction.")
    return redirect("dashboard")


# --------------------------------------------------------------------------- #
# Connections (Access + PostgreSQL)
# --------------------------------------------------------------------------- #
@staff_member_required
def connections(request):
    s = AgentSettings.get_solo()
    if request.method == "POST":
        s.access_db_path = request.POST.get("access_db_path", "").strip()
        if request.POST.get("access_db_password"):
            s.access_db_password = request.POST["access_db_password"]
        s.pg_host = request.POST.get("pg_host", "").strip()
        s.pg_port = int(request.POST.get("pg_port") or 5432)
        s.pg_dbname = request.POST.get("pg_dbname", "").strip()
        s.pg_user = request.POST.get("pg_user", "").strip()
        if request.POST.get("pg_password"):
            s.pg_password = request.POST["pg_password"]
        s.pg_sslmode = request.POST.get("pg_sslmode", "prefer").strip()
        s.state_dir = request.POST.get("state_dir", "").strip()
        s.save()
        messages.success(request, "Connection settings saved.")
        return redirect("connections")
    return render(request, "syncadmin/connections.html", {"s": s})


@staff_member_required
@require_POST
def test_connection(request, which):
    s = AgentSettings.get_solo()
    if which == "access":
        res = conn_svc.test_access(s.access_db_path, s.access_db_password)
    elif which == "postgres":
        res = conn_svc.test_postgres(
            s.pg_host, s.pg_port, s.pg_dbname, s.pg_user, s.pg_password, s.pg_sslmode
        )
    else:
        res = conn_svc.TestResult(False, "Unknown connection.")
    (messages.success if res.ok else messages.error)(request, f"{which.title()}: {res.message}")
    return redirect("connections")


# --------------------------------------------------------------------------- #
# Settings + Email
# --------------------------------------------------------------------------- #
@staff_member_required
def settings_view(request):
    s = AgentSettings.get_solo()
    if request.method == "POST":
        s.dry_run = request.POST.get("dry_run") == "on"
        s.save()
        messages.success(request, "Settings saved.")
        return redirect("settings")
    return render(request, "syncadmin/settings.html", {"s": s})


@staff_member_required
def email_settings(request):
    s = AgentSettings.get_solo()
    if request.method == "POST":
        s.alerts_enabled = request.POST.get("alerts_enabled") == "on"
        s.smtp_host = request.POST.get("smtp_host", "").strip()
        s.smtp_port = int(request.POST.get("smtp_port") or 587)
        s.smtp_user = request.POST.get("smtp_user", "").strip()
        if request.POST.get("smtp_password"):
            s.smtp_password = request.POST["smtp_password"]
        s.smtp_use_tls = request.POST.get("smtp_use_tls") == "on"
        s.alert_from = request.POST.get("alert_from", "").strip()
        s.alert_recipients = request.POST.get("alert_recipients", "").strip()
        s.save()
        messages.success(request, "Email settings saved.")
        return redirect("email_settings")
    return render(request, "syncadmin/email.html", {"s": s})


@staff_member_required
@require_POST
def test_email(request):
    s = AgentSettings.get_solo()
    ok, msg = mailer.send_test(s)
    (messages.success if ok else messages.error)(request, msg)
    return redirect("email_settings")


# --------------------------------------------------------------------------- #
# Schedule
# --------------------------------------------------------------------------- #
@staff_member_required
def schedule_view(request):
    s = AgentSettings.get_solo()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "apply":
            s.sync_interval_minutes = int(request.POST.get("sync_interval_minutes") or 15)
            s.cron_expr = request.POST.get("cron_expr", "").strip()
            s.save()
            scheduler.reschedule(s)
            messages.success(request, "Schedule applied.")
        elif action == "pause":
            s.scheduler_paused = True
            s.save()
            scheduler.set_paused(True)
            messages.success(request, "Scheduler paused.")
        elif action == "resume":
            s.scheduler_paused = False
            s.save()
            scheduler.set_paused(False)
            messages.success(request, "Scheduler resumed.")
        return redirect("schedule")
    return render(request, "syncadmin/schedule.html", {
        "s": s,
        "next_run": scheduler.next_run_time(),
        "running": scheduler.get_scheduler() is not None,
    })


# --------------------------------------------------------------------------- #
# Mappings
# --------------------------------------------------------------------------- #
@staff_member_required
def mappings(request):
    from django.db.models import Q

    q = request.GET.get("q", "").strip()
    direction = request.GET.get("direction", "").strip()
    active = request.GET.get("active", "").strip()

    tables = TableMapping.objects.all()
    if q:
        tables = tables.filter(
            Q(access_table__icontains=q)
            | Q(pg_table__icontains=q)
            | Q(web_entity__icontains=q)
            | Q(key_column__icontains=q)
        )
    if direction in (TableMapping.WEB2ACCESS, TableMapping.ACCESS2WEB):
        tables = tables.filter(direction=direction)
    if active == "on":
        tables = tables.filter(is_active=True)
    elif active == "off":
        tables = tables.filter(is_active=False)

    return render(request, "syncadmin/mappings.html", {
        "tables": tables,
        "total": TableMapping.objects.count(),
        "q": q,
        "direction": direction,
        "active": active,
        "filtered": bool(q or direction or active),
    })


@staff_member_required
@require_POST
def toggle_table_mapping(request, pk):
    """Flip a table mapping's active flag from the list page, preserving filters."""
    tm = get_object_or_404(TableMapping, pk=pk)
    tm.is_active = not tm.is_active
    tm.save(update_fields=["is_active"])
    messages.success(
        request,
        f"{tm.access_table} is now {'ACTIVE' if tm.is_active else 'INACTIVE'}.",
    )
    nxt = request.POST.get("next")
    return redirect(nxt) if nxt else redirect("mappings")


@staff_member_required
def add_table_mapping(request):
    s = AgentSettings.get_solo()
    if request.method == "POST":
        direction = request.POST.get("direction", "web2access")
        access_table = request.POST.get("access_table", "").strip()
        if TableMapping.objects.filter(direction=direction, access_table=access_table).exists():
            messages.error(request, f"A {direction} mapping for {access_table} already exists.")
            return redirect("mappings")
        tm = TableMapping.objects.create(
            direction=direction,
            access_table=access_table,
            pg_table=request.POST.get("pg_table", "").strip(),
            web_entity=request.POST.get("web_entity", "").strip(),
            key_column=request.POST.get("key_column", "").strip(),
            conflict_rule=request.POST.get("conflict_rule", "web-wins"),
            cursor_column=request.POST.get("cursor_column", "").strip(),
        )
        if tm.direction == TableMapping.ACCESS2WEB:
            messages.warning(
                request,
                "Access -> web is limited to agreed Customer / application reference data and only "
                "runs if signed off in the sync contract. It stays off until the 'access -> web' "
                "toggle is enabled on the Dashboard.",
            )
        messages.success(request, f"Added mapping for {tm.access_table}.")
        return redirect("edit_table_mapping", pk=tm.pk)
    return render(request, "syncadmin/add_mapping.html", {"s": s})


@staff_member_required
def edit_table_mapping(request, pk):
    tm = get_object_or_404(TableMapping, pk=pk)
    s = AgentSettings.get_solo()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_table":
            new_direction = request.POST.get("direction", tm.direction)
            tm.pg_table = request.POST.get("pg_table", "").strip()
            tm.key_column = request.POST.get("key_column", "").strip()
            tm.cursor_column = request.POST.get("cursor_column", "").strip()
            tm.conflict_rule = request.POST.get("conflict_rule", "web-wins")
            tm.is_active = request.POST.get("is_active") == "on"
            if new_direction != tm.direction and TableMapping.objects.filter(
                direction=new_direction, access_table=tm.access_table
            ).exclude(pk=tm.pk).exists():
                messages.error(request, f"A {new_direction} mapping for {tm.access_table} already exists.")
                return redirect("edit_table_mapping", pk=pk)
            tm.direction = new_direction
            tm.save()
            if tm.direction == TableMapping.ACCESS2WEB:
                messages.warning(
                    request,
                    "Access -> web is limited to agreed Customer / application reference data and "
                    "only runs if it is signed off in the sync contract. It stays off until the "
                    "'access -> web' toggle is enabled on the Dashboard.",
                )
            messages.success(request, "Mapping saved.")
        elif action == "add_field":
            ac = request.POST.get("access_column", "").strip()
            if ac:
                FieldMapping.objects.update_or_create(
                    table=tm, access_column=ac,
                    defaults={
                        "crm_field": request.POST.get("crm_field", "").strip() or ac,
                        "role": request.POST.get("role", "sync"),
                        "is_active": True,
                    },
                )
                messages.success(request, f"Field {ac} added.")
        elif action == "update_fields":
            for f in tm.fields.all():
                f.crm_field = request.POST.get(f"crm_field_{f.id}", f.crm_field).strip()
                f.role = request.POST.get(f"role_{f.id}", f.role)
                f.is_active = request.POST.get(f"active_{f.id}") == "on"
                f.save()
            messages.success(request, "Fields updated.")
        return redirect("edit_table_mapping", pk=pk)

    return render(request, "syncadmin/edit_mapping.html", {
        "tm": tm, "s": s, "roles": FieldMapping.ROLES,
    })


@staff_member_required
@require_POST
def delete_table_mapping(request, pk):
    tm = get_object_or_404(TableMapping, pk=pk)
    name = tm.access_table
    tm.delete()
    messages.success(request, f"Deleted mapping {name}.")
    return redirect("mappings")


@staff_member_required
@require_POST
def delete_field_mapping(request, pk):
    f = get_object_or_404(FieldMapping, pk=pk)
    tm_pk = f.table_id
    f.delete()
    messages.success(request, "Field removed.")
    return redirect("edit_table_mapping", pk=tm_pk)


@staff_member_required
@require_POST
def import_csv(request):
    upload = request.FILES.get("csv_file")
    if not upload:
        messages.error(request, "No file uploaded.")
        return redirect("mappings")
    try:
        text = upload.read().decode("utf-8-sig")
        res = mapping_svc.import_csv_text(text)
        messages.success(request, f"Imported: {res['tables']} new table(s), {res['fields']} field(s).")
    except Exception as exc:
        messages.error(request, f"Import failed: {exc}")
    return redirect("mappings")


@staff_member_required
def export_csv(request):
    direction = request.GET.get("direction") or None
    text = mapping_svc.export_csv_text(direction)
    resp = HttpResponse(text, content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="sync-field-mapping-export.csv"'
    return resp


@staff_member_required
@require_POST
def reseed_from_docs(request):
    try:
        res = mapping_svc.seed_from_docs()
        messages.success(request, f"Seeded from docs: {res['tables']} new table(s), {res['fields']} field(s).")
    except Exception as exc:
        messages.error(request, f"Seed failed: {exc}")
    return redirect("mappings")


# --------------------------------------------------------------------------- #
# Live dropdown JSON endpoints
# --------------------------------------------------------------------------- #
@staff_member_required
def api_tables(request, source):
    s = AgentSettings.get_solo()
    try:
        if source == "access":
            data = conn_svc.access_tables(s.access_db_path, s.access_db_password)
        elif source == "postgres":
            data = conn_svc.pg_tables(s.pg_host, s.pg_port, s.pg_dbname, s.pg_user, s.pg_password, s.pg_sslmode)
        else:
            return JsonResponse({"error": "unknown source"}, status=400)
        return JsonResponse({"items": data})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)


@staff_member_required
def api_columns(request, source):
    s = AgentSettings.get_solo()
    table = request.GET.get("table", "")
    if not table:
        return JsonResponse({"items": []})
    try:
        if source == "access":
            data = conn_svc.access_columns(s.access_db_path, s.access_db_password, table)
        elif source == "postgres":
            data = conn_svc.pg_columns(s.pg_host, s.pg_port, s.pg_dbname, s.pg_user, s.pg_password, table, s.pg_sslmode)
        else:
            return JsonResponse({"error": "unknown source"}, status=400)
        return JsonResponse({"items": data})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #
@staff_member_required
def logs(request):
    runs = SyncRun.objects.all()[:200]
    log_tail = ""
    from django.conf import settings as dj
    try:
        if dj.SYNC_LOG_FILE.exists():
            log_tail = "".join(dj.SYNC_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-200:])
    except Exception:
        pass
    return render(request, "syncadmin/logs.html", {"runs": runs, "log_tail": log_tail})
