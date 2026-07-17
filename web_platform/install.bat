@echo off
:: ============================================================
::  SENTINEL WEB PLATFORM — WINDOWS INSTALLER
::  Run this as Administrator once on your EC2
:: ============================================================

echo.
echo  ================================================
echo   SENTINEL — Installing Web Platform
echo  ================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://python.org
    echo         Make sure to check "Add Python to PATH"
    pause
    exit /b 1
)

echo [OK] Python found
echo.

:: Install Python packages
echo [1/3] Installing Python packages...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)
echo [OK] Packages installed

:: Copy .env if not exists
if not exist .env (
    echo [2/3] Creating .env from template...
    copy .env.template .env
    echo [ACTION REQUIRED] Open .env and fill in your values, then run this script again.
    notepad .env
    pause
    exit /b 0
) else (
    echo [2/3] .env already exists — skipping
)

:: Download NSSM for running as Windows service
echo [3/3] Downloading NSSM (service manager)...
powershell -Command "Invoke-WebRequest -Uri 'https://nssm.cc/release/nssm-2.24.zip' -OutFile 'nssm.zip'" 2>nul
if exist nssm.zip (
    powershell -Command "Expand-Archive -Path nssm.zip -DestinationPath nssm_temp -Force"
    copy nssm_temp\nssm-2.24\win64\nssm.exe nssm.exe
    rmdir /s /q nssm_temp
    del nssm.zip
    echo [OK] NSSM ready
) else (
    echo [WARN] Could not download NSSM automatically.
    echo        Download manually from https://nssm.cc and place nssm.exe in this folder.
)

echo.
echo  ================================================
echo   Installation complete!
echo   Next: run  start_web.bat  to launch the server
echo  ================================================
echo.
pause
