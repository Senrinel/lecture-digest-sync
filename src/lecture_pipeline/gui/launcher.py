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

from lecture_pipeline.automation.tray import WatcherTray
from lecture_pipeline.gui.bridge import (
    get_current_config,
    get_remembered_gui_mode,
    save_remembered_gui_mode,
    set_global_tray,
)
from lecture_pipeline.gui.server import app
from lecture_pipeline.gui.window import DesktopWindow


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


def launch_gui(
    mode: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    debug: bool = False,
) -> None:
    """Launch the GUI application according to remembered or specified mode."""
    cfg = get_current_config()
    gui_cfg = cfg.get("gui", {})

    # Determine execution mode (CLI override or remembered mode from config.yaml)
    if mode:
        active_mode = save_remembered_gui_mode(mode)
    else:
        active_mode = get_remembered_gui_mode()

    server_host = host or gui_cfg.get("host", "127.0.0.1")
    server_port = int(port or gui_cfg.get("port", 8000))
    url = f"http://{server_host}:{server_port}"
    lan_ip = get_local_ip()

    print(f"\n========================================================")
    print(f"  Lecture Digest Sync - 課堂錄音辨識與筆記同步系統 (GUI)")
    print(f"========================================================")
    print(f"📌 當前執行模式：[{active_mode.upper()}] (已記憶至 config.yaml)")
    print(f"🌐 本機網址：{url}")
    if lan_ip != "127.0.0.1":
        print(f"📱 區網跨裝置網址：http://{lan_ip}:{server_port}")
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
        print("🔔 工作列系統匣小圖示 (System Tray) 已成功啟動！")
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
        print("👀 背景收件夾 (Inbox) 即時監看服務已同步在背景運作中！")
    except Exception as we:
        logger.warning(f"無法啟動背景監看線程: {we}")

    # 4. Handle execution modes
    desktop = DesktopWindow(
        url=url,
        title="Lecture Digest Sync - 課堂錄音辨識與筆記同步系統",
        width=gui_cfg.get("window_width", 1280),
        height=gui_cfg.get("window_height", 800),
    )

    try:
        if active_mode == "web":
            print(f"🌐 網頁模式啟用：正在以預設瀏覽器開啟 {url}...")
            webbrowser.open(url)
            desktop.create(hidden=True)
            desktop.start(debug=debug)
        elif active_mode == "both":
            print(f"✨ 同時模式啟用：正在以預設瀏覽器開啟 {url} 並啟動原生視窗...")
            webbrowser.open(url)
            desktop.create(hidden=False)
            print("🖥️ 啟動 Windows 10/11 原生獨立視窗 (Microsoft Edge WebView2)...")
            desktop.start(debug=debug)
        else:
            # window mode
            desktop.create(hidden=False)
            print("🖥️ 啟動 Windows 10/11 原生獨立視窗 (Microsoft Edge WebView2)...")
            desktop.start(debug=debug)

    except KeyboardInterrupt:
        print("\n🛑 收到終止訊號，系統正在結束...")
    finally:
        if tray:
            tray.stop()
        print("🛑 系統服務已順利退出。")
