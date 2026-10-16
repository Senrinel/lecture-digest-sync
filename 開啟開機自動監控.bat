@echo off
chcp 65001 >nul
title 啟用 Windows 開機背景自動監看服務

cd /d "%~dp0"

echo ==============================================================================
echo   正在啟用 Windows 開機無感自動背景監看...
echo ==============================================================================
echo.

uv run python -m lecture_pipeline.cli autostart enable --channel C1 --interval 5

echo.
pause
