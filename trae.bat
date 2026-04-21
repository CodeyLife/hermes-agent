@echo off
setlocal

title Configure Hermes MCP for Trae CN

set "HERMES_DIR=%~dp0"
if "%HERMES_DIR:~-1%"=="\" set "HERMES_DIR=%HERMES_DIR:~0,-1%"
set "TRAE_USER_DIR=%APPDATA%\Trae CN\User"
set "MCP_CONFIG=%TRAE_USER_DIR%\mcp.json"
set "TMP_PS1=%TEMP%\hermes_config_trae_%RANDOM%%RANDOM%.ps1"

echo ========================================
echo   Configure Hermes MCP for Trae CN
echo ========================================
echo.

if not exist "%HERMES_DIR%\mcp_serve.py" (
    echo [ERROR] mcp_serve.py not found in:
    echo   %HERMES_DIR%
    echo Please place this batch file in the Hermes Agent root directory.
    pause
    exit /b 1
)

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11+ and ensure python is in PATH.
    pause
    exit /b 1
)

if not exist "%TRAE_USER_DIR%" (
    mkdir "%TRAE_USER_DIR%" >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create Trae user config directory:
        echo   %TRAE_USER_DIR%
        pause
        exit /b 1
    )
)

> "%TMP_PS1%" echo $ErrorActionPreference = 'Stop'
>> "%TMP_PS1%" echo $hermesDir = $env:HERMES_DIR
>> "%TMP_PS1%" echo $target = $env:MCP_CONFIG
>> "%TMP_PS1%" echo $config = [ordered]@{
>> "%TMP_PS1%" echo   mcpServers = [ordered]@{
>> "%TMP_PS1%" echo     hermes = [ordered]@{
>> "%TMP_PS1%" echo       command = 'python'
>> "%TMP_PS1%" echo       args = @(
>> "%TMP_PS1%" echo         '-c',
>> "%TMP_PS1%" echo         ('import sys; sys.path.insert(0, r''{0}''); from mcp_serve import run_mcp_server; run_mcp_server()' -f $hermesDir)
>> "%TMP_PS1%" echo       )
>> "%TMP_PS1%" echo       cwd = $hermesDir
>> "%TMP_PS1%" echo     }
>> "%TMP_PS1%" echo   }
>> "%TMP_PS1%" echo }
>> "%TMP_PS1%" echo $json = (($config ^| ConvertTo-Json -Depth 10).Replace('\u0027', '''')) + [Environment]::NewLine
>> "%TMP_PS1%" echo $encoding = New-Object System.Text.UTF8Encoding $false
>> "%TMP_PS1%" echo [System.IO.File]::WriteAllText($target, $json, $encoding)

powershell -NoProfile -ExecutionPolicy Bypass -File "%TMP_PS1%"
set "WRITE_EXIT=%errorlevel%"
del "%TMP_PS1%" >nul 2>&1

if not "%WRITE_EXIT%"=="0" (
    echo [ERROR] Failed to write Trae MCP config.
    pause
    exit /b %WRITE_EXIT%
)

echo [OK] Trae MCP config generated:
echo   %MCP_CONFIG%
echo.
echo Hermes path:
echo   %HERMES_DIR%
echo.
echo You can now restart Trae CN and use the Hermes MCP server.
echo.
pause
