@echo off
chcp 65001 >nul
title 建立課堂錄音一鍵整理捷徑與右鍵選單

echo ==============================================================================
echo   正在為您建立【課堂錄音一鍵整理】桌面拖曳捷徑與右鍵 SendTo 選單...
echo ==============================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$WshShell = New-Object -ComObject WScript.Shell; " ^
    "$target = Join-Path $PSScriptRoot '一鍵拖曳整理.bat'; " ^
    "$deskLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '課堂錄音一鍵整理.lnk'; " ^
    "$s1 = $WshShell.CreateShortcut($deskLnk); " ^
    "$s1.TargetPath = $target; " ^
    "$s1.WorkingDirectory = $PSScriptRoot; " ^
    "$s1.Description = '拖曳錄音檔或資料夾至此圖示，自動執行語音辨識與重點筆記整理'; " ^
    "$s1.IconLocation = 'shell32.dll,266'; " ^
    "$s1.Save(); " ^
    "Write-Host '[✔] 桌面捷徑已建立：' $deskLnk -ForegroundColor Green; " ^
    "$guiTarget = Join-Path $PSScriptRoot '啟動GUI工作台.bat'; " ^
    "$guiLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '課堂錄音GUI工作台.lnk'; " ^
    "$s3 = $WshShell.CreateShortcut($guiLnk); " ^
    "$s3.TargetPath = $guiTarget; " ^
    "$s3.WorkingDirectory = $PSScriptRoot; " ^
    "$s3.Description = '啟動課堂錄音視覺化雙軌工作台 (視窗/網頁)'; " ^
    "$s3.IconLocation = 'shell32.dll,220'; " ^
    "$s3.Save(); " ^
    "Write-Host '[✔] 桌面 GUI 捷徑已建立：' $guiLnk -ForegroundColor Green; " ^
    "$sendTo = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'Microsoft\Windows\SendTo\課堂錄音一鍵整理.lnk'; " ^
    "$s2 = $WshShell.CreateShortcut($sendTo); " ^
    "$s2.TargetPath = $target; " ^
    "$s2.WorkingDirectory = $PSScriptRoot; " ^
    "$s2.Description = '傳送至課堂錄音自動整理管線'; " ^
    "$s2.IconLocation = 'shell32.dll,266'; " ^
    "$s2.Save(); " ^
    "Write-Host '[✔] 右鍵選單 (傳送到 / SendTo) 已建立：' $sendTo -ForegroundColor Green;"

echo.
echo ==============================================================================
echo   設置完成！您現在可以：
echo   1. 雙擊桌面上的「課堂錄音GUI工作台」開啟視覺化操作介面。
echo   2. 將錄音檔案或整個錄音資料夾【直接拖曳到桌面的「課堂錄音一鍵整理」圖示】。
echo   3. 或者在任何音檔或資料夾上【按右鍵 ➜ 傳送到 ➜ 課堂錄音一鍵整理】。
echo ==============================================================================
echo.
pause
