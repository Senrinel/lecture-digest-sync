"""PyWebView Windows 10/11 native window controller.

Provides a clean standalone desktop window utilizing Microsoft Edge WebView2.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

import webview


logger = logging.getLogger("lecture_pipeline.gui")


def configure_webview2_environment() -> None:
    """Configure Chromium command line switches for Microsoft Edge WebView2.
    
    Prevents memory leaks and restricts renderer memory footprint:
    1. --js-flags="--max-old-space-size=256 --expose-gc":
       Restricts V8 heap to 256MB and exposes window.gc() for manual/periodic collection.
    2. --enable-features=ResourceSaver,BatterySaverMode,HighEfficiencyModeAvailable:
       Enables Chromium memory saving features.
    3. --disable-background-timer-throttling=false:
       Enforces background throttling on timers when window is not active.
    4. --disk-cache-size=104857600:
       Limits disk cache to 100MB.
    5. --renderer-process-limit=1:
       Restricts extra renderer processes.
    """
    args = [
        '--js-flags="--max-old-space-size=256 --expose-gc"',
        '--enable-features=ResourceSaver,BatterySaverMode,HighEfficiencyModeAvailable',
        '--disable-background-timer-throttling=false',
        '--disk-cache-size=104857600',
        '--renderer-process-limit=1',
    ]
    cur = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    new_flags = [a for a in args if a.split("=")[0] not in cur]
    if new_flags:
        combined = f"{cur} {' '.join(new_flags)}".strip()
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = combined
        logger.debug(f"Configured WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: {combined}")


def trim_process_working_set() -> None:
    """Trigger Windows OS working set trim to immediately return unused memory pages."""
    if sys.platform == "win32":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.psapi.EmptyWorkingSet(handle)
        except Exception:
            pass


class DesktopWindow:
    """Manages the lifecycle of the PyWebView native desktop window."""

    def __init__(
        self,
        url: str = "http://127.0.0.1:8000",
        title: str = "Lecture Digest Sync - 課堂錄音辨識與筆記同步",
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
        self._force_close: bool = False

    def create(self, hidden: bool = False) -> webview.Window:
        """Create and configure the desktop window."""
        initial_url = "about:blank" if hidden else self.url
        self.window = webview.create_window(
            title=self.title,
            url=initial_url,
            width=self.width,
            height=self.height,
            min_size=(self.min_width, self.min_height),
            background_color="#030712",
            confirm_close=False,
            hidden=hidden,
        )

        def on_closing():
            """Intercept user clicking top-right X to keep background watcher active."""
            if not self._force_close:
                self.hide()
                return False  # Cancels window destruction, stays alive in tray
            return True

        self.window.events.closing += on_closing
        set_desktop_window(self)
        return self.window

    def destroy(self) -> None:
        """Permanently destroy window on application exit."""
        self._force_close = True
        if self.window:
            try:
                self.window.destroy()
            except Exception as e:
                logger.warning(f"Error destroying window: {e}")

    def show(self) -> None:
        """Show and bring window to front and resume page animations."""
        if self.window:
            try:
                try:
                    cur_url = self.window.get_current_url()
                except Exception:
                    cur_url = None
                if cur_url in (None, "", "about:blank"):
                    self.window.load_url(self.url)
                self.window.show()
                self.window.restore()
                try:
                    self.window.evaluate_js("window.onHostWindowShown && window.onHostWindowShown();")
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Error showing window: {e}")

        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, self.title)
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def hide(self) -> None:
        """Hide window without closing backend loop and trigger page freeze."""
        if self.window:
            try:
                try:
                    self.window.evaluate_js("window.onHostWindowHidden && window.onHostWindowHidden();")
                except Exception:
                    pass
                self.window.hide()
                trim_process_working_set()
            except Exception as e:
                logger.warning(f"Error hiding window: {e}")

    def start(self, debug: bool = False) -> None:
        """Start the PyWebView GUI event loop on Windows 10/11."""
        configure_webview2_environment()
        try:
            # On Windows 10/11, edgechromium uses system WebView2
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
    if sys.platform == "win32":
        try:
            import ctypes
            for title in [
                "Lecture Digest Sync - 課堂錄音辨識與筆記同步系統",
                "Lecture Digest Sync - 課堂錄音辨識與筆記同步",
            ]:
                hwnd = ctypes.windll.user32.FindWindowW(None, title)
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    return True
        except Exception:
            pass
    return False


def hide_desktop_window() -> bool:
    win = get_desktop_window()
    if win:
        win.hide()
        return True
    return False

