@echo off
rem SCB-OOT: Shader Cache Builder for The Legend of Zelda: Ocarina of Time Remake.
rem Usage: scb-oot.bat "C:\path\to\game.nup"     (or just double-click and paste the path)
setlocal
cd /d "%~dp0"
set "NUP=%~1"
if "%NUP%"=="" set /p "NUP=Drag your Ocarina of Time Remake .nup file here and press Enter: "
set "NUP=%NUP:"=%"
echo.
"%~dp0python\python.exe" "%~dp0tools\scb_oot.py" --nup "%NUP%" %2 %3 %4 %5 %6 %7 %8 %9
echo.
pause
