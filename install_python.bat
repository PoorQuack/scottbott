@echo off
REM ============================================================
REM Scottbott - Bootstrap embedded Python into .\python\
REM ============================================================
REM Downloads Python 3.11.9 (embeddable, portable) from python.org,
REM extracts it to .\python\, enables site-packages, installs pip,
REM and installs all requirements. Run this ONCE per machine.
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY_VERSION=3.11.9"
set "PY_ZIP_URL=https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-embed-amd64.zip"
set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"
set "PY_DIR=%~dp0python"
set "PY_EXE=%PY_DIR%\python.exe"
set "PY_ZIP=%~dp0python-embed.zip"
set "GET_PIP=%~dp0get-pip.py"

echo ============================================================
echo  Scottbott Python bootstrap
echo  Target folder: %PY_DIR%
echo ============================================================

if exist "%PY_EXE%" (
    echo [install] Python already installed at %PY_EXE%
    "%PY_EXE%" -V
    goto :install_pip
)

REM --- Download Python embeddable zip ---
echo [install] Downloading Python %PY_VERSION% embeddable...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%PY_ZIP_URL%' -OutFile '%PY_ZIP%' -UseBasicParsing"
if not exist "%PY_ZIP%" (
    echo [install] ERROR: download failed.
    pause
    exit /b 1
)

REM --- Extract ---
echo [install] Extracting to %PY_DIR%...
if exist "%PY_DIR%" rmdir /s /q "%PY_DIR%"
mkdir "%PY_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Expand-Archive -Path '%PY_ZIP%' -DestinationPath '%PY_DIR%' -Force"
del /q "%PY_ZIP%"

if not exist "%PY_EXE%" (
    echo [install] ERROR: extraction failed - %PY_EXE% not found.
    pause
    exit /b 1
)

REM --- Patch python311._pth so site-packages and 'import site' work ---
echo [install] Patching python311._pth to enable site-packages...
> "%PY_DIR%\python311._pth" (
    echo python311.zip
    echo .
    echo Lib\site-packages
    echo.
    echo # Enable importing the bot's modules from this folder
    echo ..
    echo.
    echo # Required for pip to work
    echo import site
)

:install_pip
REM --- Bootstrap pip ---
"%PY_EXE%" -m pip --version >nul 2>nul
if not %ERRORLEVEL%==0 (
    echo [install] Bootstrapping pip via get-pip.py...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%GET_PIP_URL%' -OutFile '%GET_PIP%' -UseBasicParsing"
    if not exist "%GET_PIP%" (
        echo [install] ERROR: get-pip.py download failed.
        pause
        exit /b 1
    )
    "%PY_EXE%" "%GET_PIP%"
    del /q "%GET_PIP%"
)

REM --- Install requirements ---
echo [install] Installing requirements.txt...
"%PY_EXE%" -m pip install --upgrade pip
"%PY_EXE%" -m pip install -r requirements.txt
if not %ERRORLEVEL%==0 (
    echo [install] ERROR: pip install -r requirements.txt failed.
    pause
    exit /b 1
)

REM --- discord-ext-voice-recv (also listed in requirements.txt; double-install is harmless) ---
"%PY_EXE%" -m pip show discord-ext-voice-recv >nul 2>nul
if not %ERRORLEVEL%==0 (
    echo [install] Installing discord-ext-voice-recv...
    "%PY_EXE%" -m pip install discord-ext-voice-recv
)

echo.
echo ============================================================
echo  Python bootstrap complete.
echo  Use run.bat to start the bot.
echo ============================================================
"%PY_EXE%" -V
echo.
pause
exit /b 0
