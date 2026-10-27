# Point the permanent workers.dev address at the current Cloudflare quick tunnel.
#
#   powershell -ExecutionPolicy Bypass -File scripts\update_worker.ps1            # start tunnel if needed + deploy
#   powershell -ExecutionPolicy Bypass -File scripts\update_worker.ps1 -Url https://xyz.trycloudflare.com
#
# First time only: npx wrangler login   (opens the browser to authorise your Cloudflare account)

param([string]$Url = "")

$Root = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $env:TEMP "cloudflared-admin.log"
$Cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"

function Find-TunnelUrl {
    if (-not (Test-Path $Log)) { return "" }
    $m = Select-String -Path $Log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -Last 1
    if ($m) { return $m.Matches[0].Value } else { return "" }
}

if (-not $Url) {
    $running = Get-Process cloudflared -ErrorAction SilentlyContinue
    if ($running) { $Url = Find-TunnelUrl }
    if (-not $Url) {
        Write-Host "Starting a Cloudflare tunnel to the Admin dashboard (:8502)..."
        Remove-Item $Log -ErrorAction SilentlyContinue
        $cmd = "`$host.UI.RawUI.WindowTitle='Cloudflare tunnel (admin dashboard)'; & '$Cloudflared' tunnel --url http://localhost:8502 --logfile '$Log'"
        Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd | Out-Null
        for ($i = 0; $i -lt 45 -and -not $Url; $i++) { Start-Sleep -Seconds 2; $Url = Find-TunnelUrl }
    }
}
if (-not $Url) { Write-Host "No tunnel address found. Is cloudflared running?" -ForegroundColor Red; exit 1 }

Write-Host "Tunnel: $Url"
Push-Location (Join-Path $Root "deploy\worker")
npx --yes wrangler@4 deploy --var "TUNNEL_URL:$Url"
$code = $LASTEXITCODE
Pop-Location
exit $code
