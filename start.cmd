@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The project Python environment is missing.
  pause
  exit /b 1
)
if /I "%~1"=="--check" (
    ".venv\Scripts\python.exe" -B "src\recognize.py" %*
    if errorlevel 1 pause
) else (
    start "" ".venv\Scripts\pythonw.exe" -B "src\recognize.py" %*
)
