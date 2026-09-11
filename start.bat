@echo off
chcp 65001 >nul
title 字幕工厂 - Windows 启动器
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if %errorlevel% neq 0 (
    echo.
    echo 启动发生错误，按任意键退出...
    pause >nul
)
