@echo off
chcp 65001 >nul
echo 正在停止 netease-music-mcp ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3456" ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
    echo 已结束进程 %%a
)
echo 完成。
timeout /t 2 >nul
