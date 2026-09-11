@echo off
chcp 65001 >nul
title 字幕工厂 - 桌面应用
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-desktop.ps1"
if %errorlevel% neq 0 (
    pause
)
