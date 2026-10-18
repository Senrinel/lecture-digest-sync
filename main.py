"""Unified Entry Point for Lecture Digest Sync (課堂錄音自動化 ASR 與重點筆記系統).

Simulates the behavior of the final packaged standalone executable (.exe):
1. Double-click / No args:
   - Starts Windows System Tray icon (pystray)
   - Starts background inbox watcher (watchdog)
   - Starts FastAPI backend server (uvicorn)
   - Starts GUI interface (WebView2 native window / default browser / both)
2. Audio files / folders passed (Drag & Drop):
   - Processes input audio files through end-to-end pipeline (Prep -> STT -> Clean -> Summary -> Bundle)
3. CLI subcommands (prep, stt, clean, summary, bench, autostart, etc.):
   - Dispatches to lecture_pipeline.cli Typer app
4. Shortcut setup (--setup-shortcuts):
   - Sets up Windows Desktop shortcut and SendTo context menu
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Fix stdout/stderr encoding on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure 'src' is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def setup_desktop_shortcuts() -> None:
    """Create Windows Desktop shortcut and SendTo context menu shortcut."""
    print("==============================================================================")
    print("  正在建立【課堂錄音自動化 ASR 與重點筆記系統】桌面捷徑與右鍵 SendTo 選單...")
    print("==============================================================================")

    # Use 啟動系統.bat as the target
    target_bat = PROJECT_ROOT / "啟動系統.bat"
    if not target_bat.is_file():
        # Fallback to python main.py
        target_bat = PROJECT_ROOT / "main.py"

    ps_command = f"""
    $WshShell = New-Object -ComObject WScript.Shell;
    $target = '{str(target_bat)}';
    $workDir = '{str(PROJECT_ROOT)}';
    $iconPath = Join-Path $workDir 'icon.png';
    if (-not (Test-Path $iconPath)) {{ $iconPath = 'shell32.dll,220'; }}

    $deskLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '課堂錄音自動化系統.lnk';
    $s1 = $WshShell.CreateShortcut($deskLnk);
    $s1.TargetPath = $target;
    $s1.WorkingDirectory = $workDir;
    $s1.Description = '啟動課堂錄音 ASR 系統 (支援雙擊開啟 GUI / 拖曳音檔整理)';
    $s1.IconLocation = $iconPath;
    $s1.Save();
    Write-Host '[✔] 桌面捷徑已建立：' $deskLnk -ForegroundColor Green;

    $sendTo = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'Microsoft\\Windows\\SendTo\\課堂錄音自動整理.lnk';
    $s2 = $WshShell.CreateShortcut($sendTo);
    $s2.TargetPath = $target;
    $s2.WorkingDirectory = $workDir;
    $s2.Description = '傳送至課堂錄音自動整理管線';
    $s2.IconLocation = $iconPath;
    $s2.Save();
    Write-Host '[✔] 右鍵選單 (傳送到 / SendTo) 已建立：' $sendTo -ForegroundColor Green;
    """

    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command], check=True)
        print("\n==============================================================================")
        print("  設置完成！您現在可以：")
        print("  1. 雙擊桌面上的「課堂錄音自動化系統」開啟視覺化操作介面與背景服務。")
        print("  2. 將錄音檔案或錄音資料夾【直接拖曳到桌面的捷徑圖示】自動辨識整理。")
        print("  3. 在任何音檔或資料夾上【按右鍵 ➜ 傳送到 ➜ 課堂錄音自動整理】。")
        print("==============================================================================\n")
    except Exception as e:
        print(f"❌ 建立捷徑失敗: {e}")


def main() -> None:
    args = sys.argv[1:]

    # Case 1: Setup shortcuts
    if len(args) == 1 and args[0] in ("--setup", "--setup-shortcuts", "-s"):
        setup_desktop_shortcuts()
        return

    # Case 2: No arguments or explicit GUI trigger -> Launch full GUI + Tray + Watcher
    if not args or (len(args) >= 1 and args[0] in ("gui", "-g", "--gui")):
        from lecture_pipeline.gui import launch_gui

        # Parse potential gui flags: --mode, --host, --port, --debug
        import argparse
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--mode", "-m", type=str, default=None)
        parser.add_argument("--host", "-h", type=str, default=None)
        parser.add_argument("--port", "-p", type=int, default=None)
        parser.add_argument("--debug", "-d", action="store_true", default=False)
        
        # Strip 'gui' if passed as first arg
        effective_args = args[1:] if (args and args[0] in ("gui", "-g", "--gui")) else args
        parsed_gui, _ = parser.parse_known_args(effective_args)

        launch_gui(
            mode=parsed_gui.mode,
            host=parsed_gui.host,
            port=parsed_gui.port,
            debug=parsed_gui.debug,
        )
        return

    # Case 3: Check if arguments are audio files or directories (Drag & Drop execution)
    from lecture_pipeline.drag_and_drop import SUPPORTED_AUDIO_EXTENSIONS
    is_drag_and_drop = False
    for arg in args:
        if arg.startswith("-"):
            continue
        p = Path(arg)
        if p.exists():
            if p.is_dir() or p.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
                is_drag_and_drop = True
                break

    if is_drag_and_drop:
        from lecture_pipeline.drag_and_drop import main as dnd_main
        dnd_main()
        return

    # Case 4: General CLI commands (prep, stt, clean, summary, bench, autostart, etc.)
    from lecture_pipeline.cli import app
    app()


if __name__ == "__main__":
    main()
