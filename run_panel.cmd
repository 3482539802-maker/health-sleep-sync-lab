@echo off
cd /d "%~dp0"
python -m sleep_sync_lab.panel %*
if errorlevel 1 pause
