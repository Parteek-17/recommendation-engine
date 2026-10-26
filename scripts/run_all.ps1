# Start the whole system on Windows, each service in its own PowerShell window.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1            # live
#   powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1 -DryRun    # scheduler only prints, never sends
#
# What starts (close a window to stop that service):
#   Offers API  http://127.0.0.1:8001/docs   (uvicorn offers.api:app)
#   Main API    http://127.0.0.1:8000/docs   (uvicorn api.main:app)
#   Storefront  http://localhost:8501        (streamlit run ui/app.py)
#   Telegram bot                             (python -m bot.main, only if TELEGRAM_BOT_TOKEN is set)
#   Scheduler   offer round every 15 min     (python -m pipeline.scheduler)

param(
    [switch]$DryRun,      # scheduler prints what it WOULD send
    [switch]$NoScheduler, # start everything except the scheduler
    [switch]$NoBot        # start everything except the Telegram bot
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Use the project's virtual environment if there is one.
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

# 1. Pre-flight: .env, OFFERS_API_KEY, data, trained models.
& $Python scripts\preflight.py
if ($LASTEXITCODE -ne 0) { Write-Host "Fix the items marked [!!] above, then run this again." -ForegroundColor Red; exit 1 }

# Read two settings the same way the app does (never prints secrets).
$HasBot = (& $Python -c "from common.config import settings; print(int(settings.has('TELEGRAM_BOT_TOKEN')))").Trim() -eq "1"

function Start-Window($Title, $Command) {
    # A new PowerShell window that stays open (-NoExit) so you can read errors.
    $full = "`$Host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$Root'; & '$Python' $Command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $full | Out-Null
    Write-Host "  started: $Title"
}

Write-Host ""
Write-Host "Starting services..."
Start-Window "Offers API :8001" "-m uvicorn offers.api:app --port 8001"
Start-Window "Main API :8000"   "-m uvicorn api.main:app --port 8000"

# Wait until both APIs answer before starting the things that call them.
foreach ($url in @("http://127.0.0.1:8001/health", "http://127.0.0.1:8000/health")) {
    $ok = $false
    for ($i = 0; $i -lt 90 -and -not $ok; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $url | Out-Null; $ok = $true } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ok) { Write-Host "  $url did not answer after 90 s - check its window." -ForegroundColor Yellow }
}

Start-Window "Storefront :8501" "-m streamlit run ui/app.py --server.port 8501 --server.headless true"

$BotState = "skipped (no TELEGRAM_BOT_TOKEN in .env)"
if ($NoBot) { $BotState = "skipped (-NoBot)" }
elseif ($HasBot) { Start-Window "Telegram bot" "-m bot.main"; $BotState = "running (long polling)" }

$SchedState = "skipped (-NoScheduler)"
if (-not $NoScheduler) {
    if ($DryRun) { Start-Window "Scheduler (DRY RUN)" "-m pipeline.scheduler --dry-run"; $SchedState = "DRY RUN - prints, never sends" }
    else { Start-Window "Scheduler" "-m pipeline.scheduler"; $SchedState = "LIVE - every 15 min, reco retrain 03:00" }
}

Write-Host ""
Write-Host "================ What's running where ================" -ForegroundColor Cyan
Write-Host ("  Offers API    http://127.0.0.1:8001/docs   (needs X-API-Key)")
Write-Host ("  Main API      http://127.0.0.1:8000/docs   (/health shows real vs stub modules)")
Write-Host ("  Storefront    http://localhost:8501")
Write-Host ("  Telegram bot  $BotState")
Write-Host ("  Scheduler     $SchedState")
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "Demo script: ui\DEMO.md. Stop a service by closing its window."
