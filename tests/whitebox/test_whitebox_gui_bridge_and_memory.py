"""White-box tests for GUI Bridge EventBroadcaster, DesktopWindow memory tuning, and lifecycle logic."""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from starlette.websockets import WebSocketState

from lecture_pipeline.gui.bridge import EventBroadcaster, get_remembered_gui_mode, save_remembered_gui_mode
from lecture_pipeline.gui.window import (
    DesktopWindow,
    configure_webview2_environment,
    trim_process_working_set,
)


class TestEventBroadcasterWhitebox:
    """White-box tests for EventBroadcaster connection tracking, dead client cleanup, and broadcast."""

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        broadcaster = EventBroadcaster()
        ws_mock = AsyncMock()

        await broadcaster.connect(ws_mock)
        ws_mock.accept.assert_awaited_once()
        assert ws_mock in broadcaster.active_connections

        broadcaster.disconnect(ws_mock)
        assert ws_mock not in broadcaster.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_success_and_dead_connection_purging(self):
        broadcaster = EventBroadcaster()

        # Healthy client
        healthy_ws = AsyncMock()
        healthy_ws.client_state = WebSocketState.CONNECTED

        # Broken client that throws during send_text
        broken_ws = AsyncMock()
        broken_ws.client_state = WebSocketState.CONNECTED
        broken_ws.send_text.side_effect = RuntimeError("Socket closed abruptly")

        # Client already marked disconnected
        disconnected_ws = AsyncMock()
        disconnected_ws.client_state = WebSocketState.DISCONNECTED

        broadcaster.active_connections.update([healthy_ws, broken_ws, disconnected_ws])

        msg = {"type": "log", "message": "Test broadcast"}
        await broadcaster.broadcast(msg)

        # Healthy should receive message
        healthy_ws.send_text.assert_awaited_once()

        # Broken and disconnected should be purged
        assert healthy_ws in broadcaster.active_connections
        assert broken_ws not in broadcaster.active_connections
        assert disconnected_ws not in broadcaster.active_connections

    def test_broadcast_sync_with_loop(self):
        broadcaster = EventBroadcaster()
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        broadcaster.register_loop(mock_loop)

        msg = {"type": "test"}
        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            broadcaster.broadcast_sync(msg)
            mock_run.assert_called_once()
            coro = mock_run.call_args[0][0]
            coro.close()

    def test_broadcast_sync_when_loop_closed(self):
        broadcaster = EventBroadcaster()
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = True
        broadcaster.register_loop(mock_loop)

        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            broadcaster.broadcast_sync({"type": "test"})
            mock_run.assert_not_called()


class TestDesktopWindowMemoryAndLifecycleWhitebox:
    """White-box tests for WebView2 memory configuration, working set trimming, and window events."""

    def test_configure_webview2_environment_idempotence(self, monkeypatch):
        monkeypatch.setenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        configure_webview2_environment()
        env_val1 = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        assert "--max-old-space-size=256" in env_val1
        assert "ResourceSaver" in env_val1
        assert "--renderer-process-limit=1" in env_val1

        # Second call should not duplicate arguments
        configure_webview2_environment()
        env_val2 = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        assert env_val1 == env_val2

    def test_trim_process_working_set_safe_execution(self):
        # Must execute cleanly on all platforms without raising unhandled exceptions
        try:
            trim_process_working_set()
        except Exception as exc:
            pytest.fail(f"trim_process_working_set raised an unexpected error: {exc}")

    def test_window_closing_non_blocking_behavior(self):
        win = DesktopWindow()
        created_window = MagicMock()
        closing_event = MagicMock()
        created_window.events.closing = closing_event

        with patch("webview.create_window", return_value=created_window):
            win.create(hidden=True)

        # Retrieve the handler registered via `events.closing += on_closing`
        assert closing_event.__iadd__.called
        on_closing = closing_event.__iadd__.call_args[0][0]
        assert callable(on_closing)

        # Simulate user clicking close button when force_close is False
        with patch("threading.Thread") as mock_thread:
            # Clicking X returns False (cancels window destruction to keep background watcher running)
            result = on_closing()
            assert result is False
            mock_thread.assert_called_once()

            # If force_close is True (app exit), on_closing returns True
            win._force_close = True
            assert on_closing() is True

    def test_window_standalone_closing_clean_exit(self):
        win = DesktopWindow()
        created_window = MagicMock()
        closing_event = MagicMock()
        created_window.events.closing = closing_event

        with patch("webview.create_window", return_value=created_window):
            win.create(hidden=False, standalone=True)

        on_closing = closing_event.__iadd__.call_args[0][0]
        # In standalone mode, user clicking X directly returns True to release all Chromium resources
        assert on_closing() is True

    def test_launch_window_process_invocation(self):
        from lecture_pipeline.gui.window import launch_window_process
        with patch("lecture_pipeline.utils.process.silent_popen") as mock_popen:
            ok = launch_window_process(host="127.0.0.1", port=8000)
            assert ok is True
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert "--window-only" in cmd
            assert "--host=127.0.0.1" in cmd
            assert "--port=8000" in cmd

    def test_close_external_desktop_window_safety(self):
        from lecture_pipeline.gui.window import close_external_desktop_window
        # Verify function executes without raising unhandled exceptions
        try:
            close_external_desktop_window()
        except Exception as e:
            pytest.fail(f"close_external_desktop_window raised: {e}")


class TestGuiModePersistenceWhitebox:
    """White-box verification for GUI mode get/save behaviors."""

    def test_get_remembered_gui_mode_fallbacks(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        with patch("lecture_pipeline.gui.bridge.CONFIG_FILE_PATH", cfg_file):
            # 1. Non-existent file -> default 'window'
            assert get_remembered_gui_mode() == "window"

            # 2. Valid mode 'web'
            save_remembered_gui_mode("web")
            assert get_remembered_gui_mode() == "web"

            # 3. Valid mode 'both'
            save_remembered_gui_mode("both")
            assert get_remembered_gui_mode() == "both"

            # 4. Invalid mode -> falls back to 'window'
            cfg_file.write_text("gui:\n  mode: invalid_unknown_mode\n", encoding="utf-8")
            assert get_remembered_gui_mode() == "window"
