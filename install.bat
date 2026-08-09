@echo off
rem
rem Install the GS1 Digital Link operator shell on this machine.  Double-click me.
rem
rem There is nothing to install first.  uv is a single static binary that fetches its own
rem CPython, so this works on a machine with no Python and no developer tools.  The packages
rem come from the committed uv.lock, so this machine gets the versions that were tested
rem rather than whatever resolves today.
rem
rem Safe to run again at any time.  It writes only inside this folder and inside %USERPROFILE%.
rem
rem The pinned versions below must match install.command and .github/workflows/ci.yml;
rem tests/test_packaging.py fails if they drift apart.

setlocal
cd /d "%~dp0"

set "UV_VERSION=0.11.6"
set "PYTHON_VERSION=3.11"

if not exist "pyproject.toml" (
    echo This script has to stay in the project folder: it installs the tool that sits
    echo beside it, and pyproject.toml is not here.  Move it back and try again.
    goto :failed
)

rem Use an existing uv as-is - Homebrew's, or one IT deployed - rather than installing a
rem second copy beside it.
set "UV="
for /f "delims=" %%I in ('where uv 2^>nul') do if not defined UV set "UV=%%I"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"

if not defined UV (
    echo Installing uv %UV_VERSION% ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/%UV_VERSION%/install.ps1 | iex"
    if errorlevel 1 goto :failed
    if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
)
if not defined UV (
    echo uv installed but could not be found afterwards.  Look in %USERPROFILE%\.local\bin.
    goto :failed
)
echo Using uv: %UV%

echo.
echo Fetching Python %PYTHON_VERSION% ...
"%UV%" python install %PYTHON_VERSION%
if errorlevel 1 goto :failed

echo.
echo Installing the tool and its packages from uv.lock ...
rem --locked refuses to resolve anything new: if uv.lock no longer matches pyproject.toml the
rem install stops here rather than quietly giving this machine a different set of versions.
"%UV%" sync --extra ui --locked --python %PYTHON_VERSION%
if errorlevel 1 goto :failed

echo.
echo Checking the operator shell can start ...
"%UV%" run --frozen --extra ui --python %PYTHON_VERSION% python -c "import ui.app"
if errorlevel 1 goto :failed

echo.
echo -- Done. --------------------------------------------------------------------
echo Double-click start.bat to open the operator shell.
echo.
echo Before the first run this folder also needs clients.yml and .env, which hold the
echo site settings and the credentials.  They are never part of the download - ask
echo whoever set this up for them.
echo.
pause
exit /b 0

:failed
echo.
echo -- Installation failed. -----------------------------------------------------
echo Nothing outside this folder was changed, and nothing was published.
echo Send the lines above to whoever maintains this tool.  The two failures that have
echo a known fix - a blocked download, and Windows refusing to run this file - are
echo written up in docs\operator-install.md.
echo.
pause
exit /b 1
