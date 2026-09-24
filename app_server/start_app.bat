@echo off
cd /d "%~dp0"
title Drainage Calculations App

python launch.py
if errorlevel 1 (
    echo.
    echo "python" didn't work -- trying "python3" instead...
    echo.
    python3 launch.py
)

echo.
echo The app has stopped.
pause
