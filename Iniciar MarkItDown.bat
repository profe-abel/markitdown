@echo off
title MarkItDown - cierra esta ventana para apagar
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 app.py %*
) else (
    python app.py %*
)
if errorlevel 1 pause
