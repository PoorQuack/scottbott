@echo off
REM ============================================================
REM Scottbott launcher - portable, runs from any drive
REM ============================================================
REM Uses the bundled .\python\python.exe if present.
REM On first run (no bundled Python yet), invokes install_python.bat
REM to download a portable Python 3.11 and install dependencies.
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
echo [run.bat] Working directory: %CD%

REM --- Locate Python ---
set "PYTHON_CMD="

REM 1. Prefer the bundled portable Python in .\python\
if exist "%~dp0python\python.exe" (
    set "PYTHON_CMD=%~dp0python\python.exe"
    echo [run.bat] Using bundled Python: !PYTHON_CMD!
    goto :have_python
)

REM 2. Try `py` launcher, but verify it isn't a Microsoft Store stub
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -V >nul 2>nul
    if !ERRORLEVEL!==0 (
        set "PYTHON_CMD=py"
        echo [run.bat] Using system py launcher.
        goto :have_python
    )
)

REM 3. Try `python`, but verify it isn't the Store stub
where python >nul 2>nul
if %ERRORLEVEL%==0 (
    for /f "tokens=*" %%V in ('python -V 2^>^&1') do set "PYVER=%%V"
    echo !PYVER! | findstr /i /c:"Python was not found" >nul
    if !ERRORLEVEL!==0 (
        echo [run.bat] System 'python' is the Microsoft Store stub - ignoring.
    ) else (
        echo !PYVER! | findstr /i /b /c:"Python " >nul
        if !ERRORLEVEL!==0 (
            set "PYTHON_CMD=python"
            echo [run.bat] Using system python: !PYVER!
            goto :have_python
        )
    )
)

REM --- No Python found: bootstrap a portable copy ---
echo.
echo [run.bat] No working Python found. Bootstrapping portable Python 3.11...
echo.
call "%~dp0install_python.bat"
if not %ERRORLEVEL%==0 (
    echo [run.bat] Bootstrap failed.
    pause
    exit /b 1
)
if not exist "%~dp0python\python.exe" (
    echo [run.bat] Bootstrap finished but python.exe not found. Aborting.
    pause
    exit /b 1
)
set "PYTHON_CMD=%~dp0python\python.exe"

:have_python

REM --- Verify required deps; install on demand ---
"%PYTHON_CMD%" -c "import discord, dotenv, google.genai, aiohttp, PIL" >nul 2>nul
if not %ERRORLEVEL%==0 (
    echo [run.bat] Installing/updating dependencies from requirements.txt ...
    "%PYTHON_CMD%" -m pip install --upgrade pip
    "%PYTHON_CMD%" -m pip install -r requirements.txt
    if not !ERRORLEVEL!==0 (
        echo.
        echo [run.bat] ERROR: pip install failed. See messages above.
        pause
        exit /b 1
    )
)

REM --- Run the bot ---
echo.
echo [run.bat] Starting Scottbott...
echo ============================================================
"%PYTHON_CMD%" scottbott.py
set "EXITCODE=%ERRORLEVEL%"
echo ============================================================
echo [run.bat] Bot exited with code %EXITCODE%
pause
exit /b %EXITCODE%
