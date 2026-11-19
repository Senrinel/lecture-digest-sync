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
import time
from pathlib import Path

class SafeWriter:
    """Stream wrapper that guarantees UnicodeEncodeError is NEVER raised on Windows."""
    def __init__(self, target):
        self._target = target

    def write(self, s: str):
        if not s or self._target is None:
            return 0
        try:
            return self._target.write(s)
        except (UnicodeEncodeError, UnicodeError):
            try:
                safe_s = s.encode("ascii", errors="backslashreplace").decode("ascii")
                return self._target.write(safe_s)
            except Exception:
                return len(s)
        except Exception:
            return len(s)

    def flush(self):
        try:
            if self._target is not None:
                self._target.flush()
        except Exception:
            pass

    def reconfigure(self, *args, **kwargs):
        if self._target is not None and hasattr(self._target, "reconfigure"):
            try:
                self._target.reconfigure(*args, **kwargs)
            except Exception:
                pass

    def __getattr__(self, name):
        if self._target is not None:
            return getattr(self._target, name)
        return lambda *args, **kwargs: None


# Fix stdout/stderr encoding on Windows and attach console if invoked from CLI
if sys.platform == "win32":
    if sys.stdout is None or sys.stderr is None:
        try:
            import ctypes
            if ctypes.windll.kernel32.AttachConsole(-1):
                if sys.stdout is None:
                    sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                if sys.stderr is None:
                    sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        except Exception:
            pass

    for _s_name in ("stdout", "stderr"):
        _cur = getattr(sys, _s_name, None)
        if _cur is None:
            try:
                _devnull = open(os.devnull, "w", encoding="utf-8", errors="replace")
                setattr(sys, _s_name, SafeWriter(_devnull))
            except Exception:
                setattr(sys, _s_name, SafeWriter(None))
        else:
            try:
                _cur.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
            setattr(sys, _s_name, SafeWriter(_cur))

    if sys.stdin is None:
        import io
        sys.stdin = io.StringIO("")

# Ensure 'src' is in sys.path and handle frozen executable
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    os.chdir(PROJECT_ROOT)
else:
    PROJECT_ROOT = Path(__file__).resolve().parent
    SRC_DIR = PROJECT_ROOT / "src"
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

# Proactively load .env from PROJECT_ROOT
env_file = PROJECT_ROOT / ".env"
if env_file.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_file)
    except Exception:
        pass


def setup_desktop_shortcuts() -> None:
    """Create Windows Desktop shortcut and SendTo context menu shortcut."""
    print("==============================================================================")
    print("  正在建立【課堂錄音自動化 ASR 與重點筆記系統】桌面捷徑與右鍵 SendTo 選單...")
    print("==============================================================================")

    if getattr(sys, "frozen", False):
        target_path = Path(sys.executable)
        icon_path = str(target_path)
    else:
        target_bat = PROJECT_ROOT / "啟動系統.bat"
        target_path = target_bat if target_bat.is_file() else (PROJECT_ROOT / "main.py")
        ico_file = PROJECT_ROOT / "icon.ico"
        icon_path = str(ico_file) if ico_file.is_file() else str(PROJECT_ROOT / "icon.png")

    ps_command = f"""
    $WshShell = New-Object -ComObject WScript.Shell;
    $target = '{str(target_path)}';
    $workDir = '{str(PROJECT_ROOT)}';
    $iconPath = '{icon_path}';
    if (-not (Test-Path $iconPath)) {{ $iconPath = 'shell32.dll,220'; }}

    $deskLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '課堂錄音自動化系統.lnk';
    $s1 = $WshShell.CreateShortcut($deskLnk);
    $s1.TargetPath = $target;
    $s1.WorkingDirectory = $workDir;
    $s1.Description = '啟動課堂錄音 ASR 系統 (支援雙擊開啟 GUI / 拖曳音檔整理)';
    $s1.IconLocation = $iconPath;
    $s1.Save();
    Write-Host '[OK] 桌面捷徑已建立：' $deskLnk -ForegroundColor Green;

    $sendTo = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'Microsoft\\Windows\\SendTo\\課堂錄音自動整理.lnk';
    $s2 = $WshShell.CreateShortcut($sendTo);
    $s2.TargetPath = $target;
    $s2.WorkingDirectory = $workDir;
    $s2.Description = '傳送至課堂錄音自動整理管線';
    $s2.IconLocation = $iconPath;
    $s2.Save();
    Write-Host '[OK] 右鍵選單 (傳送到 / SendTo) 已建立：' $sendTo -ForegroundColor Green;
    """

    try:
        no_win = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
            check=True,
            creationflags=no_win,
        )
        print("\n==============================================================================")
        print("  設置完成！您現在可以：")
        print("  1. 雙擊桌面上的「課堂錄音自動化系統」開啟操作介面與背景服務。")
        print("  2. 將錄音檔案或錄音資料夾直接拖曳到桌面的捷徑圖示自動辨識整理。")
        print("  3. 在任何音檔或資料夾上按右鍵 -> 傳送到 -> 課堂錄音自動整理。")
        print("==============================================================================\n")
    except Exception as e:
        print(f"[ERROR] 建立捷徑失敗: {e}")


def check_and_forward_to_existing_instance(args: list[str]) -> bool:
    """If another instance is already running (uvicorn on port 8000), forward args and activate window."""
    try:
        from lecture_pipeline.gui.bridge import get_current_config
        cfg = get_current_config()
        gui_cfg = cfg.get("gui", {})
        host = gui_cfg.get("host", "127.0.0.1")
        port = int(gui_cfg.get("port", 8000))
    except Exception:
        host = "127.0.0.1"
        port = 8000

    import json
    import urllib.request
    import urllib.error

    is_alive = False
    for endpoint in ("/api/ping", "/api/status"):
        url_probe = f"http://{host}:{port}{endpoint}"
        try:
            req = urllib.request.Request(url_probe, headers={"User-Agent": "LecturePipeline-SingleInstance"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    is_alive = True
                    break
        except urllib.error.HTTPError as he:
            if he.code == 404 and endpoint == "/api/ping":
                continue
            break
        except Exception:
            break

    if not is_alive:
        return False

    # Server is alive! Check arguments for drag-and-drop or explicit processing
    from lecture_pipeline.drag_and_drop import SUPPORTED_AUDIO_EXTENSIONS
    audio_paths = []
    folder_paths = []
    for a in args:
        if a.startswith("-"):
            continue
        p = Path(a)
        if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
            audio_paths.append(str(p.resolve()))
        elif p.is_dir():
            folder_paths.append(str(p.resolve()))

    # Forward files or folders
    if audio_paths or folder_paths:
        for fp in folder_paths:
            try:
                data = json.dumps({"folderpath": fp}).encode("utf-8")
                req = urllib.request.Request(
                    f"http://{host}:{port}/api/process/folder",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=3.0)
                print(f"[OK] 已將資料夾轉交至背景運作中的實例：{fp}")
            except Exception as e:
                print(f"[WARN] 轉交資料夾失敗: {e}")

        for ap in audio_paths:
            try:
                data = json.dumps({"filepath": ap}).encode("utf-8")
                req = urllib.request.Request(
                    f"http://{host}:{port}/api/process/file",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=3.0)
                print(f"[OK] 已將音檔轉交至背景運作中的實例：{ap}")
            except Exception as e:
                print(f"[WARN] 轉交音檔失敗: {e}")

    # Wake up / bring window or browser to front
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/api/window/show",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=2.0)
        print("[OK] 背景監控服務已在執行，已喚醒現有操作介面。")
    except Exception:
        pass

    return True


_app_mutex_handle = None
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_mutex() -> bool:
    """Acquire named Windows Mutex and return False if another GUI/daemon instance already owns it."""
    global _app_mutex_handle
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, "LecturePipelineSingleInstanceMutex")
        last_err = ctypes.get_last_error()
        if not handle:
            return True
        if last_err == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _app_mutex_handle = handle
        return True
    except Exception:
        return True


def main() -> None:
    args = sys.argv[1:]

    # Case 0: Initialize partitioned directories and run self-repair (used by Inno Setup installer)
    if len(args) >= 4 and args[0] in ("--init-paths", "-ip"):
        inbox_dir = args[1]
        exports_dir = args[2]
        courses_dir = args[3]
        record_archive_dir = args[4] if len(args) >= 5 else None

        from lecture_pipeline.utils.bootstrap import repair_and_init_environment
        res = repair_and_init_environment(
            inbox_dir=inbox_dir,
            exports_dir=exports_dir,
            courses_dir=courses_dir,
            record_archive_dir=record_archive_dir,
        )
        p = res["paths"]
        print(
            f"[OK] 設定檔已成功初始化分區路徑：\n"
            f"  Inbox: {p['inbox_dir']}\n"
            f"  Exports: {p['exports_dir']}\n"
            f"  Courses: {p['courses_dir']}\n"
            f"  RecordArchive: {p['record_archive_dir']}"
        )
        if res["repairs"]:
            print("  自癒修復項目：", "; ".join(res["repairs"]))
        return

    # Case 0.2: Explicit self-repair & preflight command
    if len(args) == 1 and args[0] in ("--repair", "repair"):
        from lecture_pipeline.utils.bootstrap import repair_and_init_environment
        res = repair_and_init_environment()
        print(f"[REPAIR_OK] 環境自檢與修復完成：{res}")
        return

    # Case 0.5: On-demand standalone UI window process (isolated from background daemon)
    if any(a == "--window-only" or a.startswith("--window-only") for a in args):
        import argparse
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--host", type=str, default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8000)
        parsed_w, _ = parser.parse_known_args(args)

        from lecture_pipeline.utils.bootstrap import is_webview2_available
        url = f"http://{parsed_w.host}:{parsed_w.port}"
        if not is_webview2_available():
            import webbrowser
            webbrowser.open(url)
            return

        from lecture_pipeline.gui.window import DesktopWindow, configure_webview2_environment
        from lecture_pipeline.gui.bridge import get_current_config
        configure_webview2_environment()
        cfg = get_current_config()
        gui_cfg = cfg.get("gui", {})
        w = gui_cfg.get("window_width", 1280)
        h = gui_cfg.get("window_height", 800)
        try:
            desktop = DesktopWindow(
                url=url,
                title="Lecture Digest Sync - 課堂錄音辨識與筆記同步系統",
                width=w,
                height=h,
            )
            desktop.create(hidden=False, standalone=True)
            desktop.start()
        except Exception:
            import webbrowser
            webbrowser.open(url)
        return

    # Case 1: Setup shortcuts
    if len(args) == 1 and args[0] in ("--setup", "--setup-shortcuts", "-s"):
        setup_desktop_shortcuts()
        return

    # Case 1.5: Test module imports (used for packaging verification)
    if len(args) == 1 and args[0] in ("--test-import", "-ti"):
        from lecture_pipeline.export import drive_publisher
        from lecture_pipeline.gui import launcher
        from lecture_pipeline.automation import watcher
        from lecture_pipeline.utils import bootstrap
        print("[TEST_OK] All critical modules imported successfully without errors!")
        return

    # Single-instance guard: check if GUI/watcher server is already running
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

    known_cli_commands = {
        "prep", "stt", "clean", "summary", "bundle", "export",
        "bench", "autostart", "status", "watch", "env-check", "eval", "doctor", "quota", "info", "upload", "drive",
    }
    is_cli_subcommand = len(args) >= 1 and args[0].lower() in known_cli_commands
    is_gui_flag = any(
        a in ("--mode", "-m", "--host", "-h", "--port", "-p", "--debug", "-d")
        or a.startswith(("--mode=", "--host=", "--port="))
        for a in args
    )
    is_gui_invocation = (
        not args
        or (len(args) >= 1 and args[0] in ("gui", "-g", "--gui"))
        or (is_gui_flag and not is_cli_subcommand)
    )
    if (is_gui_invocation or is_drag_and_drop) and check_and_forward_to_existing_instance(args):
        return

    # Ensure environment, folders, and state files are valid & self-repaired before normal launch
    try:
        from lecture_pipeline.utils.bootstrap import repair_and_init_environment
        repair_and_init_environment()
    except Exception:
        pass

    # Case 2: No arguments or explicit GUI trigger -> Launch full GUI + Tray + Watcher
    if is_gui_invocation:
        if not _acquire_single_instance_mutex():
            for _ in range(6):
                if check_and_forward_to_existing_instance(args):
                    return
                time.sleep(0.5)
            print("[OK] 背景監控服務已在執行，已略過重複啟動。")
            return
        from lecture_pipeline.gui.window import configure_webview2_environment
        configure_webview2_environment()
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
    if is_drag_and_drop:
        from lecture_pipeline.drag_and_drop import main as dnd_main
        dnd_main()
        return

    # Case 4: General CLI commands (prep, stt, clean, summary, bench, autostart, etc.)
    from lecture_pipeline.cli import app
    app()


if __name__ == "__main__":
    main()
