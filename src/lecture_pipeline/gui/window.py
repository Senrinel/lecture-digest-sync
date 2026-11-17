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

    def create(self, hidden: bool = False, standalone: bool = False) -> webview.Window:
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
            """Handle window close event."""
            if standalone:
                # Standalone UI process terminates cleanly, releasing all Chromium memory
                return True
            if not self._force_close:
                # Asynchronously trigger window hide to prevent WinForms FormClosing message loop deadlock
                import threading
                threading.Thread(target=self.hide, daemon=True).start()
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
        """Show and bring window to front."""
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
            except Exception as e:
                logger.warning(f"Error showing window: {e}")

        if sys.platform == "win32":
            try:
                import ctypes
                for title in [
                    self.title,
                    "Lecture Digest Sync - 課堂錄音辨識與筆記同步系統",
                    "Lecture Digest Sync - 課堂錄音辨識與筆記同步",
                ]:
                    hwnd = ctypes.windll.user32.FindWindowW(None, title)
                    if hwnd:
                        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def hide(self) -> None:
        """Hide window without closing backend loop and trigger resource trim."""
        if self.window:
            try:
                if sys.platform == "win32":
                    try:
                        import ctypes
                        for title in [
                            self.title,
                            "Lecture Digest Sync - 課堂錄音辨識與筆記同步系統",
                            "Lecture Digest Sync - 課堂錄音辨識與筆記同步",
                        ]:
                            hwnd = ctypes.windll.user32.FindWindowW(None, title)
                            if hwnd:
                                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                    except Exception:
                        pass
                self.window.hide()
            except Exception as e:
                logger.warning(f"Error hiding window: {e}")

        # Async memory trim so UI loop is never blocked
        def _bg_trim():
            import time
            time.sleep(0.3)
            trim_process_working_set()

        import threading
        threading.Thread(target=_bg_trim, daemon=True).start()

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


def launch_window_process(host: str = "127.0.0.1", port: int = 8000) -> bool:
    """Launch an independent, on-demand WebView2 UI subprocess."""
    try:
        from lecture_pipeline.utils.process import silent_popen
        if getattr(sys, "frozen", False):
            exe = sys.executable
            cmd = [exe, "--window-only", f"--host={host}", f"--port={port}"]
        else:
            cmd = [sys.executable, "main.py", "--window-only", f"--host={host}", f"--port={port}"]
        silent_popen(cmd)
        return True
    except Exception as e:
        logger.warning(f"Failed to launch on-demand window process: {e}")
        return False


def close_external_desktop_window() -> None:
    """Close desktop window subprocess if running."""
    if sys.platform == "win32":
        try:
            import ctypes
            for title in [
                "Lecture Digest Sync - 課堂錄音辨識與筆記同步系統",
                "Lecture Digest Sync - 課堂錄音辨識與筆記同步",
            ]:
                hwnd = ctypes.windll.user32.FindWindowW(None, title)
                if hwnd:
                    # Send WM_CLOSE = 0x0010 to request clean termination
                    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
        except Exception:
            pass


def show_desktop_window(host: str = "127.0.0.1", port: int = 8000) -> bool:
    """Activate existing desktop window or launch a lightweight on-demand UI process."""
    win = get_desktop_window()
    if win and win.window:
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
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    return True
        except Exception:
            pass

    from lecture_pipeline.utils.bootstrap import is_webview2_available
    if not is_webview2_available():
        import webbrowser
        webbrowser.open(f"http://{host}:{port}")
        return True

    return launch_window_process(host=host, port=port)


def hide_desktop_window() -> bool:
    win = get_desktop_window()
    if win:
        win.hide()
        return True
    return False

