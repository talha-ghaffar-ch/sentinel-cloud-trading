@echo off
:: ============================================================
::  SENTINEL CLOUD TRADING — NODE LAUNCHER
::  Reads credentials from .env — never hardcoded here
:: ============================================================

echo.
echo  ================================================
echo   SENTINEL CLOUD TRADING — STARTING NODES
echo  ================================================
echo.

:: Load .env file into environment variables
:: Place your .env file in the same folder as this .bat
set "ENV_FILE=%~dp0.env"
if exist "%ENV_FILE%" (
    echo [ENV] Loading credentials from .env ...
    for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        :: Skip comment lines starting with #
        set "line=%%A"
        if not "!line:~0,1!" == "#" (
            set "%%A=%%B"
        )
    )
    echo [ENV] Loaded successfully.
) else (
    echo [WARN] .env file not found at %ENV_FILE%
    echo        Create it from .env.template before running.
    echo        Environment variables must be set manually.
)

echo.

:: ── Deployment paths (edit to match your server) ─────────────
set "SENTINEL_HOME=C:\Sentinel"
set "SCRIPTS_DIR=%SENTINEL_HOME%\trading_engine"
set "MT5_NODES=%SENTINEL_HOME%\MT5_Nodes"

cd /d "%SCRIPTS_DIR%"

:: ── Node 1 ───────────────────────────────────────────────────
echo [BOOT] Starting Node 1 ^(user_01^)...
start "Sentinel Node 1 [user_01]" cmd /k python trading_engine.py ^
    --path "%MT5_NODES%\User_01\terminal64.exe" ^
    --user user_01
timeout /t 5 /nobreak >nul

:: ── Node 2 ───────────────────────────────────────────────────
echo [BOOT] Starting Node 2 ^(user_02^)...
start "Sentinel Node 2 [user_02]" cmd /k python trading_engine.py ^
    --path "%MT5_NODES%\User_02\terminal64.exe" ^
    --user user_02
timeout /t 5 /nobreak >nul

echo.
echo  ================================================
echo   All nodes launched. Check windows for status.
echo  ================================================
echo.
echo  Run db_test.py to verify database connections.
echo.
pause
