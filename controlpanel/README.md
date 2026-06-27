# OmSree Sync Agent — Control Panel (Django web admin)

A localhost web app to run and manage the MS Access ⇄ OmSree CRM sync from a browser,
with an admin login. It reuses the `agent/` engine and stores everything in a local
SQLite DB. See `../docs/admin-app-plan.md` for the design.

## What it gives you

- **Admin login** (Django auth) on `http://127.0.0.1:8787` — localhost only.
- **Connections** — MS Access path + password **and** PostgreSQL (CRM) credentials, each
  with a **Test connection** button.
- **Mappings** — table + field mappings stored in SQLite, editable in the UI. Table and
  column pickers are **dropdowns populated live from the actual databases**. Per-field
  activate/deactivate, CSV upload/download, and re-import of `docs/*.csv`.
- **Dashboard** — Sync Now / Sync Now (dry-run); on/off toggles for web→access and
  access→web; last-run status.
- **Schedule** — cron via APScheduler (every N minutes or a cron expression), live
  reschedule, pause/resume. Runs in-process while the app is open.
- **Email** — SMTP settings + a test button; failed syncs email the recipients.
- **Logs** — every run recorded, plus a `sync.log` tail.

## First-time setup

```bash
cd omsree-access-sync-agent
pip install -r requirements.txt          # Django, psycopg2-binary, APScheduler, cryptography, waitress, pyodbc

cd controlpanel
python manage.py migrate
python manage.py seed_admin --username admin --password "CHANGE-ME"   # creates the admin login
python manage.py import_mappings         # seed mappings from ../docs/sync-field-mapping*.csv
```

## Run

Development:
```bash
python manage.py runserver 127.0.0.1:8787
```

Production-ish (recommended; put this in the Windows Startup folder so it runs at logon):
```bash
waitress-serve --host 127.0.0.1 --port 8787 controlpanel.wsgi:application
```

Open <http://127.0.0.1:8787>, log in, and:
1. **Connections** → set the Access path + password and the PostgreSQL credentials → Test each.
2. **Mappings** → review the seeded tables; set each table's PostgreSQL table + key column;
   activate the fields you want; add fields via the live dropdowns.
3. **Settings** → leave *dry-run* on until you trust the diff.
4. **Dashboard** → Sync Now (dry-run), check **Logs**.
5. **Schedule** → set the frequency; **Email** → set SMTP for failure alerts.

## Notes

- Secrets (Access/PG/SMTP passwords) are encrypted at rest with Fernet; the key lives in
  `instance/secret.key` (git-ignored). Keep that file readable only by the agent's user.
- The SQLite DB, secret key, and `sync.log` live in `controlpanel/instance/` (git-ignored).
- The cron scheduler only runs while this app is running. A headless one-shot is available:
  `python manage.py sync_now [--dry-run]` (usable from OS cron / Task Scheduler if preferred).
- access→web (inbound) is wired in the UI as a toggle but the engine path is Phase G — it is
  intentionally a no-op until the inbound contract is signed off.
