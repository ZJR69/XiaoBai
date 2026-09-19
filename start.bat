@echo off
setlocal EnableDelayedExpansion
title XiaoBai Launcher
cd /d "%~dp0"

rem ============================================================
rem  XiaoBai quick launcher
rem  - starts backend  (FastAPI/uvicorn, port 8000)
rem  - starts frontend (Vite dev server,  port 5173)
rem  - opens browser
rem  already-running services are detected and skipped
rem ============================================================

set BACKEND_PORT=8000
set FRONT_PORT=5173

rem ---- [1/3] backend ----
set BACKEND_UP=0
for /f "tokens=*" %%i in ('netstat -ano ^| findstr LISTENING ^| findstr ":%BACKEND_PORT% "') do set BACKEND_UP=1

if "%BACKEND_UP%"=="1" (
  echo [1/3] Backend  : already running on port %BACKEND_PORT%, skip.
) else (
  echo [1/3] Backend  : starting on port %BACKEND_PORT% ...
  start "xiaobai-backend" cmd /k "cd /d %~dp0backend && python -m uvicorn app.main:app --port %BACKEND_PORT%"
)

rem ---- [2/3] frontend ----
set FRONT_UP=0
for /f "tokens=*" %%i in ('netstat -ano ^| findstr LISTENING ^| findstr ":%FRONT_PORT% "') do set FRONT_UP=1

if "%FRONT_UP%"=="1" (
  echo [2/3] Frontend : already running on port %FRONT_PORT%, skip.
) else (
  echo [2/3] Frontend : starting Vite dev server ...
  start "xiaobai-frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
)

rem ---- [3/3] browser ----
echo [3/3] Waiting for services, then opening browser ...
ping -n 5 127.0.0.1 >nul
start http://localhost:%FRONT_PORT%

echo.
echo ------------------------------------------------------------
echo  Done. Two console windows keep the services alive.
echo  To stop XiaoBai: just close both windows
echo  (xiaobai-backend / xiaobai-frontend).
echo ------------------------------------------------------------
echo.
echo This window closes in 8 seconds...
ping -n 9 127.0.0.1 >nul
