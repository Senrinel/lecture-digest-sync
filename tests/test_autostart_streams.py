"""Tests verifying headless / windowed autostart stream safety.

Ensures that when the application is launched at Windows startup (where sys.stdout and sys.stderr
are None in windowed mode), no AttributeError: 'NoneType' object has no attribute 'reconfigure'
is raised, and streams are safely wrapped without crashing.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch


def test_cli_and_drag_and_drop_import_with_none_streams():
    """Verify cli and drag_and_drop modules can be imported when stdout/stderr are None."""
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None

        import lecture_pipeline.cli as cli_mod
        import lecture_pipeline.drag_and_drop as dnd_mod

        # Reload to trigger module-level execution with None streams
        importlib.reload(cli_mod)
        importlib.reload(dnd_mod)

        assert cli_mod.app is not None
        assert dnd_mod.SUPPORTED_AUDIO_EXTENSIONS is not None
    finally:
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr


def test_safe_writer_functionality():
    """Verify SafeWriter methods (write, flush, reconfigure) do not raise errors even with None target."""
    root_dir = str(Path(__file__).resolve().parents[1])
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)
    import main as main_mod

    writer_none = main_mod.SafeWriter(None)
    assert writer_none.write("test") == 0
    writer_none.flush()
    writer_none.reconfigure(encoding="utf-8")
    # Calling unknown method should not crash
    assert writer_none.unknown_method() is None

    import io
    buf = io.StringIO()
    writer_buf = main_mod.SafeWriter(buf)
    writer_buf.write("Hello World\n")
    writer_buf.flush()
    writer_buf.reconfigure()
    assert buf.getvalue() == "Hello World\n"
