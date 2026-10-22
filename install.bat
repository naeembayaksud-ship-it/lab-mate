@echo off
rem Double-click to install everything and start Lab Mate.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 run.py & goto :end)
where python >nul 2>nul && (python run.py & goto :end)
echo Python 3.9 or newer was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then double-click this file again.
:end
pause
