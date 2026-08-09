@echo off
rem
rem Open the GS1 Digital Link operator shell.  Double-click me.
rem
rem Opens a desktop window bound to 127.0.0.1 - there is no shareable URL and no browser tab.
rem "start.bat --browser" serves the same pages in a browser instead, for a machine with no
rem webview available.  The console window this opens is where errors appear; closing it
rem closes the shell.
rem
rem Run install.bat first, once.

setlocal
rem Every output path in this project is built relative to the working directory
rem (output\{client}\...), so the shell has to start from the folder this script lives in.
cd /d "%~dp0"

rem Must match install.bat; tests/test_packaging.py checks that it does.
set "PYTHON_VERSION=3.11"

set "UV="
for /f "delims=" %%I in ('where uv 2^>nul') do if not defined UV set "UV=%%I"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"

if not defined UV (
    echo This machine has not been set up yet - double-click install.bat first.
    echo.
    pause
    exit /b 1
)

rem --frozen: use uv.lock exactly as committed and never update it.  Starting the app is not
rem the moment to resolve new versions of anything that talks to a live site.
"%UV%" run --frozen --extra ui --python %PYTHON_VERSION% python -m ui %*
if errorlevel 1 (
    echo.
    echo -- The operator shell exited with an error. ---------------------------------
    echo If it never opened a window, run install.bat again first.
    echo Otherwise send the lines above to whoever maintains this tool.
    echo.
    pause
    exit /b 1
)
