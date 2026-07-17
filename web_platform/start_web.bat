@echo off
:: ============================================================
::  SENTINEL WEB PLATFORM — START SERVER
::  Run as Administrator
:: ============================================================
echo.
echo  Starting Sentinel Web Platform...
echo  Access at: http://localhost
echo  Press CTRL+C to stop
echo.

cd /d "%~dp0"
python serve.py
pause
