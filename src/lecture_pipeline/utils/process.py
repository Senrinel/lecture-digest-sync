"""Subprocess execution utilities with Windows console suppression and binary path resolution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional, Union

# Constant flag to hide command console windows on Windows
NO_WINDOW_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def resolve_binary(name: str) -> str:
    """Resolve an executable binary path.

    Priority order on Windows:
    1. Already a valid local file path.
    2. Bundled portable binaries under app `bin/` or `_internal/bin/`.
    3. Direct Chocolatey lib binary (bypassing console-spawning shimgen wrappers).
    4. System PATH lookup via shutil.which.
    """
    if not name:
        return name

    # If already a valid file, return absolute path
    path_candidate = Path(name)
    if path_candidate.is_file():
        return str(path_candidate.resolve())

    stem = path_candidate.stem.lower()
    exe_name = f"{stem}.exe" if (sys.platform == "win32" and not path_candidate.suffix) else path_candidate.name

    # Check app-local bin/ directories first (portable / installer-bundled binaries)
    candidate_dirs = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidate_dirs.extend([exe_dir / "bin", exe_dir / "_internal" / "bin", exe_dir])
    else:
        proj_root = Path(__file__).resolve().parents[3]
        candidate_dirs.extend([proj_root / "bin", Path.cwd() / "bin"])

    for d in candidate_dirs:
        local_bin = d / exe_name
        if local_bin.is_file():
            return str(local_bin.resolve())

    if sys.platform == "win32":
        # Known common locations for actual ffmpeg/ffprobe installed via Chocolatey
        if stem in ("ffmpeg", "ffprobe"):
            choco_real = Path(r"C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin") / f"{stem}.exe"
            if choco_real.is_file():
                return str(choco_real.resolve())

    # Look up in system PATH
    found = shutil.which(name)
    if found:
        p_found = Path(found)
        if sys.platform == "win32" and "chocolatey" in found.lower():
            # If shutil.which pointed to a chocolatey\bin shim, attempt locating the lib binary
            alt_real = p_found.parent.parent / "lib" / "ffmpeg" / "tools" / "ffmpeg" / "bin" / p_found.name
            if alt_real.is_file():
                return str(alt_real.resolve())
        return str(p_found.resolve())

    return name


def silent_run(cmd: Union[List[str], str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """Execute subprocess.run silently without opening a console window on Windows."""
    if "creationflags" not in kwargs and NO_WINDOW_FLAGS:
        kwargs["creationflags"] = NO_WINDOW_FLAGS
    if "stdin" not in kwargs:
        kwargs["stdin"] = subprocess.DEVNULL
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        if "encoding" not in kwargs:
            kwargs["encoding"] = "utf-8"
        if "errors" not in kwargs:
            kwargs["errors"] = "replace"

    return subprocess.run(cmd, *args, **kwargs)


def silent_popen(cmd: Union[List[str], str], *args: Any, **kwargs: Any) -> subprocess.Popen:
    """Execute subprocess.Popen silently without opening a console window on Windows."""
    if "creationflags" not in kwargs and NO_WINDOW_FLAGS:
        kwargs["creationflags"] = NO_WINDOW_FLAGS
    if "stdin" not in kwargs:
        kwargs["stdin"] = subprocess.DEVNULL
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        if "encoding" not in kwargs:
            kwargs["encoding"] = "utf-8"
        if "errors" not in kwargs:
            kwargs["errors"] = "replace"

    return subprocess.Popen(cmd, *args, **kwargs)
