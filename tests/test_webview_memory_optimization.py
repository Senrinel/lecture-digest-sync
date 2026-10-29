"""Tests for Microsoft Edge WebView2 memory optimization and leak prevention."""

import os
from pathlib import Path
import pytest

from lecture_pipeline.gui.window import configure_webview2_environment, trim_process_working_set, DesktopWindow


def test_configure_webview2_environment():
    # Clear existing if any for clean test
    old_env = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS")
    try:
        if "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" in os.environ:
            del os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
        
        configure_webview2_environment()
        current = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")

        assert "--max-old-space-size=256" in current
        assert "--expose-gc" in current
        assert "ResourceSaver" in current
        assert "--disk-cache-size=104857600" in current
        assert "--renderer-process-limit=1" in current
    finally:
        if old_env is not None:
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = old_env


def test_trim_process_working_set():
    # Must run without raising any exceptions
    trim_process_working_set()


def test_index_html_memory_guards():
    tpl_path = Path("src/lecture_pipeline/gui/templates/index.html")
    assert tpl_path.is_file()
    content = tpl_path.read_text(encoding="utf-8")

    # 1. Canvas animation throttling and lifecycle
    assert "isFluidAnimationActive" in content
    assert "startFluidAnimation" in content
    assert "stopFluidAnimation" in content
    assert "cancelAnimationFrame" in content

    # 2. Host lifecycle hooks for PyWebView
    assert "window.onHostWindowHidden" in content
    assert "window.onHostWindowShown" in content

    # 3. Log node count limit (prevent infinite DOM growth)
    assert "logsEl.children.length > 100" in content

    # 4. Visibilitychange listener
    assert "document.addEventListener('visibilitychange'" in content

    # 5. Audio buffer release
    assert "playerAudioEl.load()" in content
