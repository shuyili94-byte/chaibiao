@echo off
chcp 65001 >nul
where py.exe >nul 2>nul
if errorlevel 1 (
    python.exe "%~dp0server.py" --open
) else (
    py.exe -3 "%~dp0server.py" --open
)
if errorlevel 1 pause
