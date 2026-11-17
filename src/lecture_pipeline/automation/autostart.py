"""Windows Auto-Start management module for Lecture Pipeline Watcher.

Allows enabling or disabling automatic background folder monitoring on Windows startup
using a Windows Shortcut (.lnk) for packaged frozen executables or silent VBScript for source dev mode.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

STARTUP_SHORTCUT_NAME = "課堂錄音自動化背景監控.lnk"


def get_startup_dir() -> Path:
    """Retrieve Windows Startup directory path."""
    if sys.platform == "win32":
        app_data = os.getenv("APPDATA")
        if app_data:
            return Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / ".config" / "autostart"


def get_vbs_path() -> Path:
    """Path to the legacy/dev startup VBScript file."""
    return get_startup_dir() / "LecturePipelineWatcher.vbs"


def get_shortcut_path() -> Path:
    """Path to the packaged executable Windows Startup shortcut."""
    return get_startup_dir() / STARTUP_SHORTCUT_NAME


def enable_autostart(
    project_dir: Optional[Path | str] = None,
    channel: str = "C1",
    poll_interval: int = 5,
) -> Path:
    """Register silent background watcher/GUI daemon to start with Windows."""
    startup_dir = get_startup_dir()
    startup_dir.mkdir(parents=True, exist_ok=True)

    # In packaged executable mode, create Windows .lnk shortcut matching Inno Setup task
    if getattr(sys, "frozen", False) and project_dir is None:
        exe_path = Path(sys.executable).resolve()
        work_dir = exe_path.parent
        lnk_path = get_shortcut_path()
        ico_path = work_dir / "icon.ico"
        icon_loc = str(ico_path) if ico_path.is_file() else str(exe_path)

        # Remove legacy VBS if present to avoid dual autostart
        vbs = get_vbs_path()
        if vbs.is_file():
            try:
                vbs.unlink()
            except OSError:
                pass

        ps_cmd = (
            f"$W = New-Object -ComObject WScript.Shell; "
            f"$s = $W.CreateShortcut('{lnk_path}'); "
            f"$s.TargetPath = '{exe_path}'; "
            f"$s.Arguments = '--mode web'; "
            f"$s.WorkingDirectory = '{work_dir}'; "
            f"$s.IconLocation = '{icon_loc}'; "
            f"$s.Description = '開機自動啟動課堂錄音背景監控'; "
            f"$s.Save();"
        )
        no_win = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            check=True,
            creationflags=no_win,
        )
        return lnk_path

    vbs_path = get_vbs_path()
    root = Path(project_dir).resolve() if project_dir else Path.cwd().resolve()

    # VBScript to launch uv command completely silently without any flashing CMD window
    vbs_content = f'''Dim WshShell
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{root}"
WshShell.Run "uv run python -m lecture_pipeline.cli watch --channel {channel} --interval {poll_interval}", 0, False
'''

    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)

    return vbs_path


def disable_autostart() -> bool:
    """Unregister watcher/shortcut from Windows Startup."""
    removed = False
    for target in (get_shortcut_path(), get_vbs_path()):
        if target.is_file():
            try:
                target.unlink()
                removed = True
            except OSError:
                pass
    return removed


def get_autostart_status() -> Dict[str, Any]:
    """Check current Windows auto-start status."""
    shortcut = get_shortcut_path()
    vbs_path = get_vbs_path()

    if getattr(sys, "frozen", False):
        is_enabled = shortcut.is_file() or vbs_path.is_file()
        active_path = shortcut if shortcut.is_file() else vbs_path
    else:
        is_enabled = vbs_path.is_file() or shortcut.is_file()
        active_path = vbs_path if vbs_path.is_file() else shortcut

    return {
        "enabled": is_enabled,
        "path": str(active_path),
        "startup_dir": str(get_startup_dir()),
    }
