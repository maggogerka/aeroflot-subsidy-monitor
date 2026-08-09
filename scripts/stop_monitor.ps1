$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".monitor.pid"

if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Host "Monitor is not running: .monitor.pid was not found."
    exit 0
}

$MonitorPid = [int](Get-Content -LiteralPath $PidFile -Raw)
$Process = Get-Process -Id $MonitorPid -ErrorAction SilentlyContinue
if ($Process) {
    Stop-Process -Id $MonitorPid
    Write-Host "Monitor PID $MonitorPid was stopped."
} else {
    Write-Host "Stale PID file was removed."
}
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
