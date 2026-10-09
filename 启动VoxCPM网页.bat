@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo  ShengYing - React + Go Audio Studio
echo ============================================
echo.
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run scripts\setup_voxcpm.ps1 first.
  pause
  exit /b 1
)
if not exist ".tooling\go\go\bin\go.exe" (
  echo [ERROR] Portable Go toolchain not found in .tooling\go\go\bin.
  echo Download Go for Windows amd64 from https://go.dev/dl and extract it under .tooling\go.
  pause
  exit /b 1
)
if not exist "client\node_modules\vite\bin\vite.js" (
  echo [ERROR] React dependencies are missing. Run: cd client ^&^& npm install
  pause
  exit /b 1
)
echo Building React frontend...
call npm --prefix client run build
if errorlevel 1 (
  pause
  exit /b 1
)
echo Building Go server...
.tooling\go\go\bin\go.exe build -o .tooling\shengying-server.exe .\backend
if errorlevel 1 (
  pause
  exit /b 1
)
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  set "VOXCPM_DEVICE=cpu"
  set "VOXCPM_OPTIMIZE=0"
  echo  NVIDIA GPU not detected; VoxCPM2 will run on CPU and may be slow.
)
echo Starting local service...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start_workbench.ps1"
if errorlevel 1 pause
