"""Windows system tray icon integration for background folder watcher.

Features:
1. Dynamic state badges: 閒置💤 (Green), 處理🔄 (Orange), 上傳⏫ (Blue).
2. Mouse hover tooltip with multi-line status and progress details.
3. Right-click context menu:
   - 打開 Inbox 資料夾
   - 暫停/恢復監控
   - 查看今日紀錄
   - 關閉監控
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Optional
import pystray
from PIL import Image, ImageDraw

DEFAULT_ICON_FILE = Path("icon.png").resolve()


def create_status_icon(base_path: Path, state: str = "idle") -> Image.Image:
    """Load base icon and overlay a color-coded status indicator badge."""
    size = (64, 64)
    if base_path.is_file():
        try:
            base_img = Image.open(base_path).convert("RGBA").resize(size)
        except Exception:
            base_img = Image.new("RGBA", size, (30, 144, 255, 255))
    else:
        base_img = Image.new("RGBA", size, (30, 144, 255, 255))

    # Badge overlay in bottom-right corner
    overlay = base_img.copy()
    draw = ImageDraw.Draw(overlay)

    # Color mapping for states
    colors = {
        "idle": (46, 204, 113, 255),       # Green for 閒置💤
        "processing": (243, 156, 18, 255), # Orange for 處理🔄
        "uploading": (52, 152, 219, 255),  # Blue for 上傳⏫
        "paused": (149, 165, 166, 255),    # Gray for 暫停⏸️
    }
    badge_color = colors.get(state, (46, 204, 113, 255))

    badge_box = [42, 42, 62, 62]
    draw.ellipse(badge_box, fill=badge_color, outline=(255, 255, 255, 255), width=2)
    return overlay


class WatcherTray:
    """Manages system tray lifecycle and reactive status updates."""

    def __init__(
        self,
        inbox_dir: Path | str = "inbox",
        log_file: Path | str = "logs/watcher.log",
        icon_path: Path | str = DEFAULT_ICON_FILE,
        server_url: str = "http://127.0.0.1:8000",
    ):
        self.inbox_dir = Path(inbox_dir).resolve()
        self.log_file = Path(log_file).resolve()
        self.icon_path = Path(icon_path).resolve()
        self.server_url = server_url

        self.is_paused: bool = False
        self.stop_requested: bool = False
        self.processed_count: int = 0
        self.current_state: str = "idle"
        self.current_detail: str = "準備就緒"
        self.current_progress: str = "等待音檔"

        self._lock = threading.Lock()
        self._icon: Optional[pystray.Icon] = None
        self._images = {
            "idle": create_status_icon(self.icon_path, "idle"),
            "processing": create_status_icon(self.icon_path, "processing"),
            "uploading": create_status_icon(self.icon_path, "uploading"),
            "paused": create_status_icon(self.icon_path, "paused"),
        }

    def _build_tooltip(self) -> str:
        """Construct descriptive multi-line hover tooltip."""
        icons = {
            "idle": "閒置💤",
            "processing": "處理🔄",
            "uploading": "上傳⏫",
            "paused": "已暫停⏸️",
        }
        state_label = icons.get(self.current_state, "監控中")
        lines = [
            f"ASR 課堂錄音監控 [{state_label}]",
            f"任務: {self.current_detail}",
            f"進度: {self.current_progress}",
            f"今日處理: {self.processed_count} 堂",
        ]
        return "\n".join(lines)

    def _on_show_window(self, _icon, _item):
        """Right-click menu: 顯示桌面視窗."""
        try:
            from lecture_pipeline.gui.window import show_desktop_window
            show_desktop_window()
        except Exception:
            pass

    def _on_open_web(self, _icon, _item):
        """Right-click menu: 開啟網頁版."""
        try:
            import webbrowser
            webbrowser.open(self.server_url)
        except Exception:
            pass

    def _on_both_modes(self, _icon, _item):
        """Right-click menu: 同時雙軌模式."""
        try:
            from lecture_pipeline.gui.window import show_desktop_window
            import webbrowser
            show_desktop_window()
            webbrowser.open(self.server_url)
        except Exception:
            pass

    def _on_open_inbox(self, _icon, _item):
        """Right-click menu: 打開 Inbox 資料夾."""
        try:
            self.inbox_dir.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(self.inbox_dir))
            else:
                subprocess.Popen(["explorer", str(self.inbox_dir)])
        except Exception:
            pass

    def _on_toggle_pause(self, icon, _item):
        """Right-click menu: 暫停/恢復監控."""
        with self._lock:
            self.is_paused = not self.is_paused
            new_state = "paused" if self.is_paused else "idle"
            self.current_state = new_state
            self.current_detail = "監控已暫停" if self.is_paused else "正在監看 Inbox"
            self.current_progress = "暫停中" if self.is_paused else "等待音檔"

        self._apply_state_to_icon()
        if icon:
            icon.update_menu()

    def _on_view_log(self, _icon, _item):
        """Right-click menu: 查看今日紀錄."""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.log_file.is_file():
                self.log_file.touch()
            if hasattr(os, "startfile"):
                os.startfile(str(self.log_file))
            else:
                subprocess.Popen(["notepad.exe", str(self.log_file)])
        except Exception:
            pass

    def _on_exit(self, icon, _item):
        """Right-click menu: 關閉系統."""
        with self._lock:
            self.stop_requested = True
        if icon:
            try:
                icon.stop()
            except Exception:
                pass
        try:
            from lecture_pipeline.gui.window import get_desktop_window
            win = get_desktop_window()
            if win and win.window:
                win.window.destroy()
        except Exception:
            pass
        os._exit(0)

    def _get_pause_label(self, _item) -> str:
        return "恢復監控 ▶️" if self.is_paused else "暫停監控 ⏸️"

    def _apply_state_to_icon(self) -> None:
        if not self._icon:
            return
        img = self._images.get(self.current_state, self._images["idle"])
        self._icon.icon = img
        self._icon.title = self._build_tooltip()

    def update_status(
        self,
        state: str = "idle",
        detail: str = "",
        progress: str = "",
    ) -> None:
        """Thread-safe update of tray badge and hover tooltip."""
        with self._lock:
            if not self.is_paused or state == "paused":
                self.current_state = state
            if detail:
                self.current_detail = detail
            if progress:
                self.current_progress = progress

        self._apply_state_to_icon()

    def increment_processed(self) -> None:
        """Increment count of processed lectures today."""
        with self._lock:
            self.processed_count += 1
        self._apply_state_to_icon()

    def start(self) -> None:
        """Launch the system tray icon detached in background thread."""
        menu = pystray.Menu(
            pystray.MenuItem("顯示桌面視窗 🖥️", self._on_show_window),
            pystray.MenuItem("開啟瀏覽器網頁 🌐", self._on_open_web),
            pystray.MenuItem("同時雙軌模式 🔄", self._on_both_modes),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打開 Inbox 資料夾 📂", self._on_open_inbox),
            pystray.MenuItem(self._get_pause_label, self._on_toggle_pause),
            pystray.MenuItem("查看今日紀錄 📋", self._on_view_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("關閉並退出系統 🛑", self._on_exit),
        )

        initial_img = self._images.get(self.current_state, self._images["idle"])
        self._icon = pystray.Icon(
            name="ASRLectureWatcher",
            icon=initial_img,
            title=self._build_tooltip(),
            menu=menu,
        )
        self._icon.run_detached()

    def stop(self) -> None:
        """Shut down the system tray icon."""
        with self._lock:
            self.stop_requested = True
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
