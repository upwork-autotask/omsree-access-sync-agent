# Deploying the Sync Agent to the client's local server

The agent must run on a Windows machine that can reach **both** databases:
- the **MS Access** file (local disk or a share), and
- the **OmSree CRM PostgreSQL** at `13.53.188.122:5432` (over the internet).

That machine should be **always on** and the user **logged in** (the scheduler runs
in-process; see [Auto-start](#5-auto-start-as-a-windows-service)).

All configuration — connection settings, encrypted DB passwords, and every field
mapping (incl. the access→web value-maps) — lives in `controlpanel/instance/`, which is
**gitignored**. Moving that folder moves the whole configured state.

---

## 0. Prerequisites on the client machine

1. **Python 3.13 or 3.14 (64-bit)** — https://www.python.org/downloads/ (tick "Add to
   PATH"). Both are validated (3.13 with Django 6.0.7, and 3.14.6). Django 6.0 (pinned in
   `requirements.txt`) needs Python **3.12+**, so do not use 3.10/3.11. If a machine has
   multiple Pythons, target the right one explicitly with `py -3.14` / `py -3.13`.
2. **Microsoft Access Database Engine 2016 redistributable (64-bit)** — required so
   `pyodbc` has the `Microsoft Access Driver (*.mdb, *.accdb)`. Must match Python's
   bitness (64-bit). https://www.microsoft.com/download/details.aspx?id=54920
   → run `accessdatabaseengine_X64.exe`. Without this, every Access read/write fails.
3. **Git** (optional, for cloning) — or copy the folder manually.
4. Network: the machine can open `13.53.188.122:5432`, and can read/write the
   Access `.accdb`.

Verify Python + driver:
```powershell
python --version
python -c "import pyodbc; print([d for d in pyodbc.drivers() if 'Access' in d])"
# expect: ['Microsoft Access Driver (*.mdb, *.accdb)']
```

---

## 1. Get the code

```powershell
cd C:\OmSree                     # any install location
git clone https://github.com/upwork-autotask/omsree-access-sync-agent.git
cd omsree-access-sync-agent
git checkout feat/sync-agent-and-webapp-plan
```
(Or copy the whole repo folder to the client machine on a USB/secure transfer.)

## 2. Install Python dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 3. Move the configured state (`instance/`)  ← the important step

The `instance/` folder holds `agent.sqlite3` (all settings + mappings), `secret.key`
(decrypts the stored DB passwords), and `django_secret.key`. It is **not** in git.

- **Copy** `controlpanel/instance/` from the current machine to the same path on the
  client machine (secure channel — it contains credentials).
- `agent.sqlite3` + `secret.key` must travel **together** (the key decrypts the
  passwords in the DB; one without the other is useless).

> No `instance/` yet? Then it's a fresh setup: run the migration in step 4, start the
> server, log in, and re-enter Connections + import mappings from `docs/*.csv`. Copying
> `instance/` is strongly preferred — it preserves the value-maps and access→web wiring.

## 4. Point at the client's Access file & apply migrations

```powershell
cd controlpanel
python manage.py migrate
```
Then start the server once (step 5 quick test) and on **Connections** set the
**Access DB path** to wherever the `.accdb` lives on THIS machine (it will differ from
the dev machine). Click **Test** for both Access and PostgreSQL — both must pass.

## 5. Auto-start as a Windows service

Register a Scheduled Task that runs the waitress launcher (`serve.py`), auto-starts on
logon, and restarts on crash. Run in PowerShell (adjust the two paths):

```powershell
$pyw  = (Get-Command python).Source -replace 'python.exe','pythonw.exe'
$dir  = "C:\OmSree\omsree-access-sync-agent\controlpanel"
$name = "OmSreeSyncAgent"
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
$action   = New-ScheduledTaskAction -Execute $pyw -Argument "serve.py" -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Start-ScheduledTask -TaskName $name
```

Verify:
```powershell
Invoke-WebRequest http://127.0.0.1:8787/login/ -UseBasicParsing | Select StatusCode  # 200
```
Open http://127.0.0.1:8787 — log in with the admin account.

To run **before** any user logs in (true boot-time), re-register with
`-LogonType Password` and a stored Windows password (`New-ScheduledTaskPrincipal
-LogonType Password`). Otherwise it starts at logon.

---

## 6. CRM network whitelist  ← easy-to-miss gotcha

The CRM PostgreSQL only accepts connections from **whitelisted IPs** (`pg_hba.conf`).
The client server's **public IP** must be added, or every PostgreSQL connection fails
with `no pg_hba.conf entry for host "<ip>"`.

On the CRM host (`ubuntu@13.53.188.122`), as the DB admin, add the client's public IP to
`pg_hba.conf` for user `sync_agent` / db `omsree_crm`, then `SELECT pg_reload_conf();`.
Confirm the client's public IP first (from the client machine: browse to any "what is my
IP" service, or `curl ifconfig.me`).

---

## 7. Post-deploy checklist

- [ ] `pyodbc.drivers()` lists the Access driver (step 0)
- [ ] Connections → **Test** passes for **both** Access and PostgreSQL
- [ ] Client server's public IP is in the CRM `pg_hba.conf` (step 6)
- [ ] Access DB path on Connections points at the client machine's `.accdb`
- [ ] Scheduled task `OmSreeSyncAgent` state = Running; http://127.0.0.1:8787 returns 200
- [ ] Dashboard shows the expected mappings and a "next scheduled" time
- [ ] Decide **dry-run** behaviour (below) and set it intentionally

### Sync behaviour to confirm
- **Global dry-run** (Settings): if ON, scheduled runs only *preview* (write nothing).
  For automatic writes, turn it OFF — but a web→access write will fail while someone has
  the Access file open (lock). Common setup: automatic **access→web**, manual **Sync Now**
  for **web→access** when Access is closed.
- **Schedule**: interval / cron on the Schedule screen.

## Security notes
- `instance/` carries the Fernet key + encrypted DB passwords — transfer over a secure
  channel, never commit it, and lock down the folder's file permissions.
- Keep the CRM SSH key (`.pem`) and DB passwords off the repo and out of shared drives.
- The control panel binds to `127.0.0.1` only (not exposed to the network).
