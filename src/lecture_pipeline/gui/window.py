"""PyWebView Windows 10/11 native window controller.

Provides a clean standalone desktop window utilizing Microsoft Edge WebView2.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import webview


logger = logging.getLogger("lecture_pipeline.gui")


class DesktopWindow:
    """Manages the lifecycle of the PyWebView native desktop window."""

    def __init__(
        self,
        url: str = "http://127.0.0.1:8000",
        title: str = "Lecture Digest Sync - 課堂錄音辨識與筆記同步系統",
        width: int = 1280,
        height: int = 800,
        min_width: int = 960,
        min_height: int = 640,
    ) -> None:
        self.url = url
        self.title = title
        self.width = width
        self.height = height
        self.min_width = min_width
        self.min_height = min_height
        self.window: Optional[webview.Window] = None

    def create(self, hidden: bool = False) -> webview.Window:
        """Create and configure the desktop window."""
        self.window = webview.create_window(
            title=self.title,
            url=self.url,
            width=self.width,
            height=self.height,
            min_size=(self.min_width, self.min_height),
            background_color="#030712",
            confirm_close=False,
            hidden=hidden,
        )
        set_desktop_window(self)
        return self.window

    def show(self) -> None:
        """Show and bring window to front."""
        if self.window:
            try:
                self.window.show()
                self.window.restore()
            except Exception as e:
                logger.warning(f"Error showing window: {e}")

    def hide(self) -> None:
        """Hide window without closing backend loop."""
        if self.window:
            try:
                self.window.hide()
            except Exception as e:
                logger.warning(f"Error hiding window: {e}")

    def start(self, debug: bool = False) -> None:
        """Start the PyWebView GUI event loop on Windows 10/11."""
        try:
            # On Windows 10, edgechromium uses system WebView2
            gui_engine = "edgechromium" if sys.platform == "win32" else None
            webview.start(gui=gui_engine, debug=debug)
        except Exception as e:
            logger.error(f"PyWebView window error: {e}", exc_info=True)
            # Fallback to standard start
            webview.start(debug=debug)


_active_desktop_window: Optional[DesktopWindow] = None


def set_desktop_window(win: DesktopWindow) -> None:
    global _active_desktop_window
    _active_desktop_window = win


def get_desktop_window() -> Optional[DesktopWindow]:
    global _active_desktop_window
    return _active_desktop_window


def show_desktop_window() -> bool:
    win = get_desktop_window()
    if win:
        win.show()
        return True
    return False


def hide_desktop_window() -> bool:
    win = get_desktop_window()
    if win:
        win.hide()
        return True
    return False

