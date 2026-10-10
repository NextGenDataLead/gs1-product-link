@echo off
rem
rem Install the GS1 Digital Link operator shell on this machine.  Double-click me.
rem
rem There is nothing to install first.  uv is a single static binary that fetches its own
rem CPython, so this works on a machine with no Python and no developer tools.  The packages
rem come from the committed uv.lock, so this machine gets the versions that were tested
rem rather than whatever resolves today.
rem
rem Safe to run again at any time.  To update, download the new release, unzip it anywhere and
rem run its installer: it finds this machine's data folder and leaves it as it is.  It writes
rem inside this folder, inside %USERPROFILE% and - on a first install only - a data folder in your
rem home folder plus a one-line note of where it is (lib/install_location.py has the rules).
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
echo Finding this machine's data folder ...
rem Settings, credentials and the record of what was published live there, not in this folder,
rem so a newer release can replace this one without touching them.  It refuses - and stops the
rem install - rather than start a second, empty one beside data it cannot account for.
"%UV%" run --frozen --extra ui --python %PYTHON_VERSION% python -m scripts.data_folder --install >nul
if errorlevel 1 goto :failed

echo.
echo -- Done. --------------------------------------------------------------------
echo Double-click start.bat to open the operator shell.
echo.
echo On a new installation, fill in the site settings and credentials on the Setup
echo screen - or ask whoever set this up for the clients.yml and .env that go in the
echo data folder named above.  They are never part of the download.
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
