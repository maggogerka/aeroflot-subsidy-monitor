$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    throw "Python was not found. Install Python 3.11+ and add it to PATH."
}

$VersionText = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$VersionParts = $VersionText.Split(".")
if (([int]$VersionParts[0] -lt 3) -or (([int]$VersionParts[0] -eq 3) -and ([int]$VersionParts[1] -lt 11))) {
    throw "Python 3.11 or newer is required. Found Python $VersionText."
}

if (-not (Test-Path -LiteralPath ".venv")) {
    Write-Host "Creating the virtual environment..."
    & python -m venv .venv
}

. ".\.venv\Scripts\Activate.ps1"
& python -m pip install --upgrade pip
& python -m pip install -r requirements.txt
& python -m playwright install chromium

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env. Fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
} else {
    Write-Host ".env already exists and was not overwritten."
}

if (-not (Test-Path -LiteralPath "config.yaml")) {
    Copy-Item -LiteralPath "config.example.yaml" -Destination "config.yaml"
    Write-Host "Created config.yaml."
} else {
    Write-Host "config.yaml already exists and was not overwritten."
}

New-Item -ItemType Directory -Force -Path "browser_profile", "logs", "artifacts" | Out-Null
Write-Host ""
Write-Host "Installation completed."
Write-Host "1. Run the configuration wizard: .\scripts\configure.bat"
Write-Host "2. Test Telegram: .\.venv\Scripts\python.exe -m app.main --test-alert"
Write-Host "3. Run one check: .\scripts\run_once.bat"
