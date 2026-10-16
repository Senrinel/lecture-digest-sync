@echo off
chcp 65001 >nul
title 課堂錄音自動化 ASR 與重點筆記系統

cd /d "%~dp0"

uv run python -m lecture_pipeline.drag_and_drop %*
