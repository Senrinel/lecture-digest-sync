@echo off
chcp 65001 >nul
title 關閉 Windows 開機背景自動監看服務

cd /d "%~dp0"

echo ==============================================================================
echo   正在關閉 Windows 開機自動背景監看...
echo ==============================================================================
echo.

uv run python -m lecture_pipeline.cli autostart disable

echo.
pause
