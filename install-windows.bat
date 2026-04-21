@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   Hermes Agent Windows Installer
echo ========================================
echo.

:: Check Python
echo [1/5] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11+
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo [OK] Python %PYTHON_VERSION% detected
echo.

:: Check project directory
echo [2/5] Checking project directory...
if not exist "pyproject.toml" (
    echo [ERROR] Please run this script in the Hermes Agent root directory
    pause
    exit /b 1
)
echo [OK] Project directory verified
echo.

:: Create virtual environment
echo [3/5] Creating virtual environment...
if exist "venv" (
    echo [INFO] Virtual environment already exists, skipping
) else (
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
)
echo.

:: Install dependencies
echo [4/5] Installing dependencies (this may take a few minutes)...
call venv\Scripts\activate.bat
pip install -e ".[all,dev]" --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)
echo [OK] Dependencies installed
echo.

:: Verify installation
echo [5/5] Verifying installation...
hermes --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Hermes installation verification failed
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Installation Complete!
echo ========================================
echo.
echo Usage:
echo   1. Activate virtual environment:
echo      call venv\Scripts\activate.bat
echo.
echo   2. Run Hermes:
echo      hermes              ^<^# Classic CLI
echo      hermes --tui        ^<^# Modern TUI (recommended)
echo.
echo   3. First-time setup:
echo      hermes setup        ^<^# Full setup wizard
echo      hermes model        ^<^# Choose AI model
echo.
echo ========================================
echo.

pause
