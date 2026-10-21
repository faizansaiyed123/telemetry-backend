
param(
    [switch]$Start,
    [switch]$Stop,
    [switch]$Restart
)

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$PidFile = Join-Path $RepoRoot ".telemetry.pids.json"

function Stop-All {
    $pids = @()
    if (Test-Path $PidFile) {
        $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
        Remove-Item $PidFile -Force
    }

    foreach ($entry in $pids) {
        $procId = $entry.pid
        $name = $entry.name
        try {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                Write-Host "Stopped $name (pid $procId)" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "Stopped $name (pid $procId) - already gone" -ForegroundColor DarkYellow
        }
    }

    foreach ($port in 8000, 8787) {
        $found = netstat -ano | Select-String "LISTENING.*:$port " | ForEach-Object {
            ($_ -split '\s+')[-1]
        }
        foreach ($p in $found) {
            try {
                Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
                Write-Host "Killed leftover on port $port (pid $p)" -ForegroundColor DarkYellow
            } catch {}
        }
    }

    Write-Host "All services stopped." -ForegroundColor Red
}

function Start-All {
    if (Test-Path $PidFile) {
        Write-Host "Services already running (pid file exists). Use -Stop first or -Restart." -ForegroundColor Yellow
        return
    }

    if (-not (Test-Path $VenvPython)) {
        Write-Error "Backend venv not found at $VenvPython. Run 'uv sync' in backend/telemetry-backend first."
        return
    }

    $secret = if ($env:WEBHOOK_SIGNING_SECRET) { $env:WEBHOOK_SIGNING_SECRET } else { "local-dev-webhook-signing-secret-32chars" }
    $pids = @()

    $be = Start-Process -FilePath $VenvPython -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--log-level","info" -WorkingDirectory $RepoRoot -NoNewWindow -PassThru
    $pids += @{name="backend"; pid=$be.Id}
    Write-Host "Backend  started (pid $($be.Id)) :8000" -ForegroundColor Green

    $wr = Start-Process -FilePath $VenvPython -ArgumentList "examples/webhook_receiver.py" -WorkingDirectory $RepoRoot -NoNewWindow -PassThru -Environment @{WEBHOOK_SIGNING_SECRET=$secret}
    if (-not $wr.Id) {
        $wr = Start-Process -FilePath "cmd" -ArgumentList "/c","set WEBHOOK_SIGNING_SECRET=$secret & $VenvPython examples/webhook_receiver.py" -WorkingDirectory $RepoRoot -NoNewWindow -PassThru
    }
    $pids += @{name="webhook"; pid=$wr.Id}
    Write-Host "Webhook started (pid $($wr.Id)) :8787" -ForegroundColor Green

    $pids | ConvertTo-Json | Out-File $PidFile -Encoding UTF8
    Write-Host "`nBackend services running. Run '.\run.ps1 -Stop' to stop them." -ForegroundColor Cyan
}

if ($Stop) { Stop-All; return }
if ($Restart) { Stop-All; Start-Sleep -Seconds 2; Start-All; return }
if ($Start) { Start-All; return }

Start-All
