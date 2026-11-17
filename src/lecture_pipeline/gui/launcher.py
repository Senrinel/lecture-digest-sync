"""Dual-mode launcher for Lecture Digest Sync GUI.

Supports:
1. 'window': Standalone native desktop window (Microsoft Edge WebView2 on Windows 10/11)
2. 'web': Local FastAPI Web server opened in default browser
3. 'both': Standalone desktop window + background Web server for multi-device / browser access

Automatically remembers the last execution mode in config.yaml.
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

import uvicorn

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        if _s is not None:
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

from lecture_pipeline.automation.tray import WatcherTray
from lecture_pipeline.gui.bridge import (
    get_current_config,
    get_remembered_gui_mode,
    save_remembered_gui_mode,
    set_global_tray,
)
from lecture_pipeline.gui.server import app
from lecture_pipeline.gui.window import DesktopWindow, configure_webview2_environment, trim_process_working_set


logger = logging.getLogger("lecture_pipeline.gui")


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if the given port is already occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def get_local_ip() -> str:
    """Retrieve local machine LAN IP address for multi-device access."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def run_uvicorn_server(host: str, port: int) -> None:
    """Run Uvicorn ASGI server."""
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


def start_memory_watchdog(desktop: Optional[DesktopWindow] = None, interval_sec: int = 900) -> threading.Thread:
    """Periodic memory watchdog to prevent RAM bloat during multi-day background runs."""
    def _watchdog_loop():
        import gc
        while True:
            time.sleep(interval_sec)
            try:
                gc.collect()
                trim_process_working_set()
            except Exception:
                pass

    t = threading.Thread(target=_watchdog_loop, daemon=True, name="MemoryWatchdog")
    t.start()
    return t


def launch_gui(
    mode: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    debug: bool = False,
) -> None:
    """Launch the GUI application according to remembered or specified mode."""
    configure_webview2_environment()
    cfg = get_current_config()
    gui_cfg = cfg.get("gui", {})

    # Determine execution mode (CLI override or remembered mode from config.yaml)
    if mode:
        active_mode = save_remembered_gui_mode(mode)
    else:
        active_mode = get_remembered_gui_mode()

    from lecture_pipeline.utils.bootstrap import is_webview2_available
    if active_mode in ("window", "both") and not is_webview2_available():
        print("[WARN] 未偵測到 Microsoft Edge WebView2 Runtime，已自動切換為「網頁瀏覽器模式」。")
        active_mode = "web"

    server_host = host or gui_cfg.get("host", "127.0.0.1")
    server_port = int(port or gui_cfg.get("port", 8000))
    url = f"http://{server_host}:{server_port}"
    lan_ip = get_local_ip()

    print(f"\n========================================================")
    print(f"  Lecture Digest Sync - 課堂錄音辨識與筆記同步系統 (GUI)")
    print(f"========================================================")
    print(f"[模式] 當前執行模式：[{active_mode.upper()}] (已記憶至 config.yaml)")
    print(f"[網址] 本機網址：{url}")
    if lan_ip != "127.0.0.1":
        print(f"[區網] 區網跨裝置網址：http://{lan_ip}:{server_port}")
    print(f"========================================================\n")

    # 1. Initialize and start Windows System Tray Icon
    tray = None
    try:
        paths = cfg.get("paths", {})
        tray = WatcherTray(
            inbox_dir=paths.get("inbox_dir", "inbox"),
            log_file=Path(paths.get("logs_dir", "logs")) / "watcher.log",
            server_url=url,
        )
        tray.start()
        set_global_tray(tray)
        print("[系統匣] 工作列系統匣小圖示 (System Tray) 已成功啟動！")
    except Exception as te:
        logger.warning(f"無法啟動系統匣小圖示: {te}")

    # 2. Start Uvicorn ASGI server in background thread if not already running
    if not is_port_in_use(server_port, server_host):
        server_thread = threading.Thread(
            target=run_uvicorn_server,
            args=(server_host, server_port),
            daemon=True,
        )
        server_thread.start()
        time.sleep(0.5)

    # 3. Start background inbox watcher thread so GUI is actively watching inbox
    try:
        from lecture_pipeline.automation.watcher import watch_inbox
        watcher_thread = threading.Thread(
            target=watch_inbox,
            kwargs={
                "inbox_dir": paths.get("inbox_dir", "inbox"),
                "enable_tray": False,
                "upload_drive": True,
            },
            daemon=True,
        )
        watcher_thread.start()
        print("[監看] 背景收件夾 (Inbox) 即時監看服務已同步在背景運作中！")
    except Exception as we:
        logger.warning(f"無法啟動背景監看線程: {we}")

    # 4. Handle execution modes via on-demand UI architecture (Ultra-Low Footprint)
    start_memory_watchdog()

    from lecture_pipeline.gui.window import close_external_desktop_window, show_desktop_window

    try:
        if active_mode == "web":
            print(f"[啟動] 網頁模式啟用：正在以預設瀏覽器開啟 {url}...")
            webbrowser.open(url)
        elif active_mode == "both":
            print(f"[啟動] 同時模式啟用：正在以預設瀏覽器開啟 {url} 並啟動原生視窗...")
            webbrowser.open(url)
            show_desktop_window(host=server_host, port=server_port)
        else:
            # window mode
            print("[視窗] 啟動 Windows 10/11 原生獨立視窗 (按需載入)...")
            show_desktop_window(host=server_host, port=server_port)

        # 5. Keep headless daemon core alive in ultra-low footprint mode (~60MB RAM, 0% CPU)
        while True:
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[結束] 收到終止訊號，系統正在結束...")
    finally:
        close_external_desktop_window()
        if tray:
            tray.stop()
        print("[退出] 系統服務已順利退出。")
