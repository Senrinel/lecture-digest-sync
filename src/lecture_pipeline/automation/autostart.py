"""Windows Auto-Start management module for Lecture Pipeline Watcher.

Allows enabling or disabling automatic background folder monitoring on Windows startup
using silent VBScript launcher in the Startup directory or Task Scheduler.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Any


def get_startup_dir() -> Path:
    """Retrieve Windows Startup directory path."""
    if sys.platform == "win32":
        app_data = os.getenv("APPDATA")
        if app_data:
            return Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / ".config" / "autostart"


def get_vbs_path() -> Path:
    """Path to the startup VBScript file."""
    return get_startup_dir() / "LecturePipelineWatcher.vbs"


def enable_autostart(
    project_dir: Optional[Path | str] = None,
    channel: str = "C1",
    poll_interval: int = 5,
) -> Path:
    """Register silent background watcher to start with Windows.

    Creates a VBScript in the Windows Startup folder that launches the watcher
    silently with window style 0 (completely hidden from taskbar and desktop).
    """
    startup_dir = get_startup_dir()
    startup_dir.mkdir(parents=True, exist_ok=True)
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
    """Unregister watcher from Windows Startup."""
    vbs_path = get_vbs_path()
    if vbs_path.is_file():
        try:
            vbs_path.unlink()
            return True
        except OSError:
            return False
    return False


def get_autostart_status() -> Dict[str, Any]:
    """Check current Windows auto-start status."""
    vbs_path = get_vbs_path()
    is_enabled = vbs_path.is_file()

    return {
        "enabled": is_enabled,
        "path": str(vbs_path),
        "startup_dir": str(get_startup_dir()),
    }
