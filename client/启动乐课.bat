@echo off
chcp 936 >nul
cd /d "%~dp0"
title 乐课 - 启动中

if not exist ".venv\Scripts\pythonw.exe" goto :novenv
if not exist "乐课.py" goto :noscript

start "" ".venv\Scripts\pythonw.exe" "乐课.py"
exit /b 0

:novenv
echo.
echo  [错误] 没有找到 .venv 虚拟环境。
echo.
echo  请在本目录打开命令行，依次执行以下命令完成初始化：
echo      1) python -m venv .venv
echo      2) .venv\Scripts\python.exe -m pip install -r requirements.txt
echo      3) .venv\Scripts\python.exe -m pip install PySide6-Essentials
echo.
pause
exit /b 1

:noscript
echo.
echo  [错误] 没有找到 乐课.py，请确认文件完整。
echo.
pause
exit /b 1
