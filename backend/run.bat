@echo off
chcp 65001 >nul
title 字幕工厂 - 后端服务
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
if %errorlevel% neq 0 (
    pause
)
