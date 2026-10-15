@echo off
chcp 936 >nul
cd /d "%~dp0"
title 乐课 - 调试模式（带控制台）
".venv\Scripts\python.exe" "乐课.py"
echo.
echo 程序已退出，按任意键关闭窗口。
pause >nul
