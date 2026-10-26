@echo off
chcp 65001 >nul
title netease-music-mcp
cd /d "%~dp0"

set "PY=C:\Users\dao\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================
echo   netease-music-mcp v3.2.1
echo   服务地址: http://127.0.0.1:3456/mcp
echo   健康检查: http://127.0.0.1:3456/health
echo   关闭本窗口或按 Ctrl+C 即可停止服务
echo ============================================
echo.

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"%PY%" server\mcp-server\server.py

echo.
echo 服务已停止。
pause
