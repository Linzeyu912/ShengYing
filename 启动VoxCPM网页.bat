@echo off
chcp 65001 >nul
cd /d D:\YH\ShengYing
echo 正在启动 VoxCPM Web 界面...
echo 启动后用浏览器打开: http://127.0.0.1:8808/
echo （首次使用"自动转录"功能会下载 ASR 模型，属正常现象）
echo.
.venv\Scripts\python.exe third_party\VoxCPM\app.py --model-id models\VoxCPM2 --host 127.0.0.1 --port 8808
pause
