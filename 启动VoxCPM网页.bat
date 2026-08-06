@echo off
cd /d D:\YH\ShengYing
echo ============================================
echo  ShengYing - VoxCPM Web UI
echo ============================================
echo.
echo  Starting server, please wait...
echo  Then open in browser: http://127.0.0.1:8808/
echo.
echo  Note: first use of ASR auto-transcribe will
echo  download the ASR model (one time only).
echo.
.venv\Scripts\python.exe third_party\VoxCPM\app.py --model-id models\VoxCPM2 --host 127.0.0.1 --port 8808
pause
