@echo off
setlocal
cd /d "%~dp0"

set "CONFIG_ONLY="
set "DEV="
for %%a in (%*) do (
    if /i "%%~a"=="--config" set "CONFIG_ONLY=1"
    if /i "%%~a"=="--dev" set "DEV=1"
)

if defined CONFIG_ONLY goto :copy_configs

echo Checking Python...
if not defined PYTHON if exist .python set /p PYTHON=<.python
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found ^(set PYTHON or add it to PATH^).
    exit /b 1
)
"%PYTHON%" -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.11-3.14 is required.
    exit /b 1
)

echo Creating virtual environment...
if not exist "env\Scripts\python.exe" (
    "%PYTHON%" -m venv env
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create the virtual environment.
        exit /b 1
    )
)

echo Installing dependencies...
if defined DEV (
    env\Scripts\pip.exe install -r requirements-dev.txt
) else (
    env\Scripts\pip.exe install -r requirements.txt
)
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    exit /b 1
)

echo Registering package in editable mode...
env\Scripts\pip.exe install -e .
if %errorlevel% neq 0 (
    echo [ERROR] Failed to register package.
    exit /b 1
)

:copy_configs
echo Copying configuration templates...
if not exist ".env" (
    if exist ".env.dist" (
        copy ".env.dist" ".env" >nul
        echo [+] Created .env from template.
    )
)
if not exist "config.toml" (
    if exist "config.dist.toml" (
        copy "config.dist.toml" "config.toml" >nul
        echo [+] Created config.toml from template.
    )
)
if not exist "tasks.json" (
    if exist "tasks.dist.json" (
        copy "tasks.dist.json" "tasks.json" >nul
        echo [+] Created tasks.json from template.
    )
)
if not exist "manifests\unlock-manifest.json" (
    if exist "manifests\unlock-manifest.dist.json" (
        copy "manifests\unlock-manifest.dist.json" "manifests\unlock-manifest.json" >nul
        echo [+] Created manifests\unlock-manifest.json from template.
    )
)
if not exist "manifests\idle-manifest.json" (
    if exist "manifests\idle-manifest.dist.json" (
        copy "manifests\idle-manifest.dist.json" "manifests\idle-manifest.json" >nul
        echo [+] Created manifests\idle-manifest.json from template.
    )
)

echo.
echo [OK] Setup complete.
echo Next: review the generated config files, then launch with run.bat (see README).
endlocal
