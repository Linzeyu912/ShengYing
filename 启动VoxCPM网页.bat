@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo  ShengYing - VoxCPM2 Audio Studio
echo ============================================
echo.
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run scripts\setup_voxcpm.ps1 first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,13) else 1)"
if errorlevel 1 (
  echo [ERROR] .venv must use Python 3.10-3.12. Rebuild it with Python 3.11 or 3.12.
  pause
  exit /b 1
)
if not exist "models\VoxCPM2" (
  echo [ERROR] models\VoxCPM2 not found. Run scripts\setup_voxcpm.ps1 first.
  pause
  exit /b 1
)
echo  Starting server, please wait...
echo  Then open in browser: http://127.0.0.1:8317/
echo.
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  set "VOXCPM_DEVICE=cpu"
  set "VOXCPM_OPTIMIZE=0"
  echo  NVIDIA GPU not detected; VoxCPM2 will run on CPU and may be slow.
)
.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8317
pause
