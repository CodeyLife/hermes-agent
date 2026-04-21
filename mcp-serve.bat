@echo off
title Hermes MCP Server

:: Change to script directory
cd /d "%~dp0"

:: Check virtual environment
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found.
    echo Please run install-windows.bat first.
    pause
    exit /b 1
)

:: Activate virtual environment
call venv\Scripts\activate.bat

:: Start Hermes MCP Server
echo Starting Hermes MCP Server...
hermes mcp serve

:: Pause on error
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Hermes MCP Server exited with code: %errorlevel%
    pause
)
