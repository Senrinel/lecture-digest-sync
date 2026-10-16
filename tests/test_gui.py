"""Unit and integration tests for Lecture Digest Sync GUI dual-mode architecture.

Tests:
1. Mode persistence in config.yaml ('window', 'web', 'both').
2. FastAPI RESTful endpoints (status, config, timetable, history, watcher, upload).
3. WebSocket event broadcaster.
4. PyWebView window controller configuration.
5. Launcher port/network utilities.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest
import yaml
from fastapi.testclient import TestClient

from lecture_pipeline.gui.bridge import (
    EventBroadcaster,
    broadcaster,
    get_archived_lectures,
    get_current_config,
    get_full_system_status,
    get_lecture_content,
    get_remembered_gui_mode,
    get_timetable_rules,
    is_watcher_paused,
    save_remembered_gui_mode,
    save_timetable_rules,
    set_watcher_paused,
    update_pipeline_config,
)
from lecture_pipeline.gui.launcher import get_local_ip, is_port_in_use
from lecture_pipeline.gui.server import app
from lecture_pipeline.gui.window import DesktopWindow


@pytest.fixture
def isolated_gui_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """Provide an isolated environment with temporary config.yaml, inbox, work, and archive."""
    config_file = tmp_path / "config.yaml"
    inbox_dir = tmp_path / "inbox"
    work_dir = tmp_path / "work"
    archive_dir = tmp_path / "archive"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    initial_config = {
        "pipeline": {
            "default_channel": "C1",
        },
        "paths": {
            "inbox_dir": str(inbox_dir),
            "work_dir": str(work_dir),
            "archive_dir": str(archive_dir),
        },
        "timetable": {
            "enabled": True,
            "Monday": [{"time": "09:00-11:00", "course": "作業系統"}],
            "Wednesday": [{"time": "14:00-17:00", "course": "人工智慧概論"}],
        },
        "gui": {
            "mode": "window",
            "host": "127.0.0.1",
            "port": 8000,
            "auto_open_browser": True,
            "window_width": 1280,
            "window_height": 800,
        },
    }
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(initial_config, f, allow_unicode=True, sort_keys=False)

    monkeypatch.setattr("lecture_pipeline.gui.bridge.CONFIG_FILE_PATH", config_file)
    monkeypatch.setattr("lecture_pipeline.gui.server.STATIC_DIR", tmp_path / "static")

    # Create dummy static dir & icon.png for testing
    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdummyicon")

    yield tmp_path


# ===========================================================================
# 1. Mode Persistence & Configuration Tests
# ===========================================================================

def test_get_and_save_remembered_gui_mode(isolated_gui_env: Path) -> None:
    """Verify that GUI mode is remembered and accurately persisted into config.yaml."""
    # 1. Initial read should be 'window'
    assert get_remembered_gui_mode() == "window"

    # 2. Switch to 'web'
    saved = save_remembered_gui_mode("web")
    assert saved == "web"
    assert get_remembered_gui_mode() == "web"

    # 3. Switch to 'both'
    saved = save_remembered_gui_mode("both")
    assert saved == "both"
    assert get_remembered_gui_mode() == "both"

    # 4. Switch back to 'window' with uppercase and extra spaces
    saved = save_remembered_gui_mode("  WINDOW  ")
    assert saved == "window"
    assert get_remembered_gui_mode() == "window"

    # 5. Invalid mode falls back to default 'window'
    saved = save_remembered_gui_mode("invalid_mode_xyz")
    assert saved == "window"
    assert get_remembered_gui_mode() == "window"


def test_update_pipeline_config(isolated_gui_env: Path) -> None:
    """Verify partial configuration updates."""
    updated = update_pipeline_config({"pipeline": {"default_channel": "C2"}})
    assert updated["pipeline"]["default_channel"] == "C2"

    cfg = get_current_config()
    assert cfg["pipeline"]["default_channel"] == "C2"


# ===========================================================================
# 2. Timetable CRUD Tests
# ===========================================================================

def test_timetable_crud(isolated_gui_env: Path) -> None:
    """Verify timetable reading and updating."""
    # Read initial
    tt = get_timetable_rules()
    assert tt["enabled"] is True
    assert len(tt["rules"]) == 2
    courses = [r["course"] for r in tt["rules"]]
    assert "作業系統" in courses
    assert "人工智慧概論" in courses

    # Save modified rules
    new_rules = [
        {"day": "Friday", "time": "10:00-12:00", "course": "密碼學與網路安全"}
    ]
    saved_tt = save_timetable_rules(rules=new_rules, enabled=False)
    assert saved_tt["enabled"] is False
    assert len(saved_tt["rules"]) == 1
    assert saved_tt["rules"][0]["course"] == "密碼學與網路安全"
    assert saved_tt["rules"][0]["dayZh"] == "週五"


# ===========================================================================
# 3. Watcher and System Status Diagnostics
# ===========================================================================

def test_watcher_toggle_and_status(isolated_gui_env: Path) -> None:
    """Verify watcher pause toggle and system diagnostics."""
    assert is_watcher_paused() is False
    set_watcher_paused(True)
    assert is_watcher_paused() is True
    set_watcher_paused(False)
    assert is_watcher_paused() is False

    status = get_full_system_status()
    assert "gpu" in status
    assert "network" in status
    assert "google_docs" in status
    assert "disk" in status
    assert "watcher" in status
    assert status["watcher"]["paused"] is False


# ===========================================================================
# 4. History and Archive Scanning Tests
# ===========================================================================

def test_history_scanning_and_detail(isolated_gui_env: Path) -> None:
    """Verify archived lecture folder discovery and file streaming."""
    archive_dir = isolated_gui_env / "archive"
    lecture_folder = archive_dir / "261017_計算機網路"
    lecture_folder.mkdir(parents=True, exist_ok=True)

    (lecture_folder / "01_raw.txt").write_text("這是逐字稿生稿", encoding="utf-8")
    (lecture_folder / "02_cleaned.txt").write_text("這是 AI 整理後的逐字稿", encoding="utf-8")
    (lecture_folder / "03_summary.md").write_text("# 摘要\n重點一：網路七層架構", encoding="utf-8")
    (lecture_folder / "audio.opus").write_bytes(b"OggS\x00dummyopusdata")

    # Test get_archived_lectures
    lectures = get_archived_lectures()
    assert len(lectures) >= 1
    matched = next((l for l in lectures if l["id"] == "261017_計算機網路"), None)
    assert matched is not None
    assert matched["title"] == "計算機網路"
    assert matched["has_summary"] is True
    assert matched["has_cleaned"] is True
    assert matched["has_audio"] is True
    assert matched["audio_filename"] == "audio.opus"

    # Test get_lecture_content
    detail = get_lecture_content("261017_計算機網路")
    assert detail is not None
    assert detail["id"] == "261017_計算機網路"
    assert "重點一：網路七層架構" in detail["summary"]
    assert "這是 AI 整理後的逐字稿" in detail["cleaned"]
    assert detail["audio_url"] == "/api/audio/261017_計算機網路"

    # Non-existent lecture
    assert get_lecture_content("non_existent_folder_xyz") is None


# ===========================================================================
# 5. FastAPI REST API Integration Tests
# ===========================================================================

def test_fastapi_endpoints(isolated_gui_env: Path) -> None:
    """Test all FastAPI endpoints using TestClient."""
    client = TestClient(app)

    # 1. Serve index page
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]

    # 2. Serve icon
    res = client.get("/icon.png")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"

    # 3. Status API
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "gpu" in data
    assert "watcher" in data

    # 4. Mode API - switch to 'web'
    res = client.post("/api/mode", json={"mode": "web"})
    assert res.status_code == 200
    assert res.json()["mode"] == "web"
    assert get_remembered_gui_mode() == "web"

    # 5. Config API
    res = client.get("/api/config")
    assert res.status_code == 200
    assert "pipeline" in res.json()

    res = client.post("/api/config", json={"params": {"pipeline": {"default_channel": "C3"}}})
    assert res.status_code == 200
    assert res.json()["config"]["pipeline"]["default_channel"] == "C3"

    # 6. Timetable API
    res = client.get("/api/timetable")
    assert res.status_code == 200
    assert "rules" in res.json()

    res = client.post(
        "/api/timetable",
        json={
            "enabled": True,
            "rules": [
                {"day": "Tuesday", "time": "13:00-15:00", "course": "演算法"}
            ],
        },
    )
    assert res.status_code == 200
    assert len(res.json()["timetable"]["rules"]) == 1

    # 7. Watcher toggle API
    res = client.post("/api/watcher/toggle")
    assert res.status_code == 200
    assert res.json()["paused"] is True

    res = client.post("/api/watcher/toggle")
    assert res.status_code == 200
    assert res.json()["paused"] is False

    # 8. History API
    res = client.get("/api/history")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    # 9. Non-existent history detail & audio stream
    res = client.get("/api/history/missing_lecture_123")
    assert res.status_code == 404

    res = client.get("/api/audio/missing_lecture_123")
    assert res.status_code == 404

    # 10. Process file - missing file check
    res = client.post("/api/process/file", json={"filepath": "non_existent_audio.wav"})
    assert res.status_code == 400

    # 11. Upload file
    dummy_file = io.BytesIO(b"RIFF\x00\x00\x00\x00WAVEfmt test audio data")
    res = client.post(
        "/api/process/upload",
        files={"file": ("upload_test.wav", dummy_file, "audio/wav")},
        data={"channel_id": "C1"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    inbox_file = isolated_gui_env / "inbox" / "upload_test.wav"
    assert inbox_file.is_file()


# ===========================================================================
# 6. WebSocket Broadcaster Test
# ===========================================================================

def test_websocket_broadcaster() -> None:
    """Verify WebSocket connection and live message broadcasting."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as ws:
            # Initial greeting sent on connect
            data = ws.receive_json()
            assert data.get("type") == "connected"
            assert len(broadcaster.active_connections) >= 1


# ===========================================================================
# 7. PyWebView Controller & Network Helper Tests
# ===========================================================================

def test_desktop_window_configuration() -> None:
    """Verify DesktopWindow properties without invoking blocking native display."""
    window = DesktopWindow(
        url="http://127.0.0.1:8000",
        title="Test Window",
        width=1024,
        height=768,
    )
    assert window.url == "http://127.0.0.1:8000"
    assert window.title == "Test Window"
    assert window.width == 1024
    assert window.height == 768


def test_launcher_network_utils() -> None:
    """Verify network utility functions."""
    ip = get_local_ip()
    assert isinstance(ip, str)
    assert len(ip) > 0

    # Test an unlikely used high port (e.g., 59123) is not in use
    in_use = is_port_in_use(59123)
    assert isinstance(in_use, bool)
