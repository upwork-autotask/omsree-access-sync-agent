# One-click registration of the OmSreeSyncAgent auto-start Scheduled Task.
# Auto-detects the install folder and a suitable 64-bit Python (prefers 3.14, then 3.13),
# verifies the dependencies are installed for it, then registers + starts the task.
$ErrorActionPreference = "Stop"
$name = "OmSreeSyncAgent"
$dir  = Join-Path $PSScriptRoot "controlpanel"     # serve.py lives here

if (-not (Test-Path (Join-Path $dir "serve.py"))) {
    Write-Host "ERROR: serve.py not found under $dir" -ForegroundColor Red
    Write-Host "Run this from inside the omsree-access-sync-agent folder." ; exit 1
}

# --- find a Python that HAS the dependencies installed ---
$pyExe = $null
$cands = @()
foreach ($v in @("3.14","3.13")) {
    try { $p = (& py -$v -c "import sys;print(sys.executable)" 2>$null); if ($p) { $cands += $p } } catch {}
}
$fallback = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($fallback) { $cands += $fallback }

foreach ($c in $cands) {
    & $c -c "import django, pyodbc, psycopg2, cryptography, apscheduler, waitress" 2>$null
    if ($LASTEXITCODE -eq 0) { $pyExe = $c; break }
}
if (-not $pyExe) {
    Write-Host "ERROR: no Python (3.13/3.14, 64-bit) has the dependencies installed." -ForegroundColor Red
    Write-Host "Install them first, e.g.:  py -3.14 -m pip install -r requirements.txt" ; exit 1
}
$pyw = $pyExe -replace 'python\.exe$','pythonw.exe'
Write-Host "Python (with deps): $pyExe"
Write-Host "Serve dir         : $dir"

# --- register + start the task (runs as the logged-in user, at logon, auto-restart) ---
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
$action    = New-ScheduledTaskAction -Execute $pyw -Argument "serve.py" -WorkingDirectory $dir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
              -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
Start-ScheduledTask -TaskName $name

Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskName $name).State
Write-Host "Task '$name' state: $state" -ForegroundColor Green
try {
    $code = (Invoke-WebRequest http://127.0.0.1:8787/login/ -UseBasicParsing -TimeoutSec 10).StatusCode
    Write-Host "Control panel HTTP: $code   ->  open http://127.0.0.1:8787" -ForegroundColor Green
} catch {
    Write-Host "Control panel not responding yet (give it a few seconds, then open http://127.0.0.1:8787)" -ForegroundColor Yellow
}
Write-Host "`nNext: open the control panel, set the Access DB path on Connections, and click Test."
