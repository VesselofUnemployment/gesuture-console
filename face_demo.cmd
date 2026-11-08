@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Set up the project Python environment first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -B "src\face_detection_demo.py" %*
if errorlevel 1 pause
