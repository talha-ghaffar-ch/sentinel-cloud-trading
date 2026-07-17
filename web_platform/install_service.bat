@echo off
:: ============================================================
::  SENTINEL — INSTALL AS WINDOWS SERVICE
::  Runs automatically on server reboot
::  Run as Administrator
:: ============================================================

set SERVICE_NAME=SentinelWeb
set APP_DIR=%~dp0
set PYTHON_PATH=

:: Find Python path
for /f "delims=" %%i in ('where python') do set PYTHON_PATH=%%i

echo.
echo  Installing Sentinel Web as Windows Service...
echo  Python: %PYTHON_PATH%
echo  App dir: %APP_DIR%
echo.

if not exist "%APP_DIR%nssm.exe" (
    echo [ERROR] nssm.exe not found. Run install.bat first.
    pause
    exit /b 1
)

:: Remove old service if exists
"%APP_DIR%nssm.exe" stop %SERVICE_NAME% 2>nul
"%APP_DIR%nssm.exe" remove %SERVICE_NAME% confirm 2>nul

:: Install service
"%APP_DIR%nssm.exe" install %SERVICE_NAME% "%PYTHON_PATH%"
"%APP_DIR%nssm.exe" set %SERVICE_NAME% AppParameters "%APP_DIR%serve.py"
"%APP_DIR%nssm.exe" set %SERVICE_NAME% AppDirectory "%APP_DIR%"
"%APP_DIR%nssm.exe" set %SERVICE_NAME% DisplayName "Sentinel Cloud Trading Web"
"%APP_DIR%nssm.exe" set %SERVICE_NAME% Description "Sentinel Flask web platform"
"%APP_DIR%nssm.exe" set %SERVICE_NAME% Start SERVICE_AUTO_START
"%APP_DIR%nssm.exe" set %SERVICE_NAME% AppStdout "%APP_DIR%logs\web_stdout.log"
"%APP_DIR%nssm.exe" set %SERVICE_NAME% AppStderr "%APP_DIR%logs\web_stderr.log"
"%APP_DIR%nssm.exe" set %SERVICE_NAME% AppRotateFiles 1
"%APP_DIR%nssm.exe" set %SERVICE_NAME% AppRotateSeconds 86400

:: Create logs folder
mkdir "%APP_DIR%logs" 2>nul

:: Start service
"%APP_DIR%nssm.exe" start %SERVICE_NAME%

echo.
echo  ================================================
echo   Service installed and started!
echo   Name: %SERVICE_NAME%
echo   Auto-starts on reboot.
echo.
echo   To stop:    nssm stop SentinelWeb
echo   To restart: nssm restart SentinelWeb
echo   Logs:       %APP_DIR%logs\
echo  ================================================
echo.
pause
