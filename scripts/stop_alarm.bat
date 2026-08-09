@echo off
if not exist "%~dp0..\artifacts" mkdir "%~dp0..\artifacts"
type nul > "%~dp0..\artifacts\STOP_SOUND.flag"
echo Sound alarm acknowledgement was sent.
