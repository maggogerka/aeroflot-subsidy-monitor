@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Сначала выполните scripts\install.ps1
  exit /b 1
)
".venv\Scripts\python.exe" -m app.main
endlocal
