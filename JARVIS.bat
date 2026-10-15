@echo off
rem Start JARVIS without a console window.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m jarvis %*
