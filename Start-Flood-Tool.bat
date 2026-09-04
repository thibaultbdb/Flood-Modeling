@echo off
REM Windows: double-click this file to start the Flood Risk Mapping Platform.
cd /d "%~dp0"
where py >nul 2>&1 && (py launch.py & goto :eof)
where python >nul 2>&1 && (python launch.py & goto :eof)
echo.
echo   Python is not installed on this PC.
echo.
echo   1. Go to  https://www.python.org/downloads/
echo   2. Download and install it. On the FIRST installer screen,
echo      tick the box "Add python.exe to PATH" before clicking Install.
echo   3. Then double-click this file again.
echo.
start https://www.python.org/downloads/
pause
