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
    open_file_in_desktop,
    resolve_export_lecture_file,
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
    export_dir = tmp_path / "exports"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)

    initial_config = {
        "pipeline": {
            "default_channel": "C1",
        },
        "paths": {
            "inbox_dir": str(inbox_dir),
            "work_dir": str(work_dir),
            "archive_dir": str(archive_dir),
        },
        "export": {
            "export_dir": str(export_dir),
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
    """Verify export notes discovery, archive exclusion, and audio cross-lookup."""
    export_dir = isolated_gui_env / "exports"
    archive_dir = isolated_gui_env / "archive"
    
    # 1. Export directory lecture
    lecture_folder = export_dir / "261017_計算機網路"
    lecture_folder.mkdir(parents=True, exist_ok=True)
    (lecture_folder / "01_raw.txt").write_text("這是逐字稿生稿", encoding="utf-8")
    (lecture_folder / "02_cleaned.txt").write_text("這是 AI 整理後的逐字稿", encoding="utf-8")
    (lecture_folder / "03_summary.md").write_text("# 摘要\n重點一：網路七層架構", encoding="utf-8")
    
    # 2. Archive-only directory (MUST NOT be displayed in history)
    archive_only_folder = archive_dir / "261018_數位信號"
    archive_only_folder.mkdir(parents=True, exist_ok=True)
    (archive_only_folder / "03_summary.md").write_text("# 數位信號筆記", encoding="utf-8")

    # 3. Audio file stored in archive for the export lecture
    arch_audio_dir = archive_dir / "計算機網路" / "261017"
    arch_audio_dir.mkdir(parents=True, exist_ok=True)
    (arch_audio_dir / "audio.opus").write_bytes(b"OggS\x00dummyopusdata")

    # Test get_archived_lectures
    lectures = get_archived_lectures()
    lecture_ids = [l["id"] for l in lectures]
    assert "261017_計算機網路" in lecture_ids
    assert "261018_數位信號" not in lecture_ids  # Strictly excluded!

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


# ===========================================================================
# 8. Task Control, System Desktop & Glossary API Tests
# ===========================================================================

def test_task_control_and_system_desktop_endpoints(isolated_gui_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify task pause/abort, folder opening, file opening, and glossary endpoints."""
    client = TestClient(app)

    # 1. Task control when idle
    res = client.post("/api/task/pause")
    assert res.status_code == 200
    assert res.json()["status"] == "error"

    res = client.post("/api/task/abort")
    assert res.status_code == 200
    assert res.json()["status"] == "error"

    # 2. System folder open
    # Mock open_folder_in_desktop so it doesn't pop up Explorer during test
    monkeypatch.setattr("lecture_pipeline.gui.server.open_folder_in_desktop", lambda p: True)
    monkeypatch.setattr("lecture_pipeline.gui.server.open_file_in_desktop", lambda p: True)
    monkeypatch.setattr("lecture_pipeline.gui.server.browse_folder_dialog", lambda p: "C:/Mock/Path")

    res = client.post("/api/system/open-folder", json={"path": str(isolated_gui_env / "inbox")})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 3. System file open
    summary_dummy = isolated_gui_env / "summary.md"
    summary_dummy.write_text("# Test Summary", encoding="utf-8")
    res = client.post("/api/system/open-file", json={"path": str(summary_dummy)})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 4. Browse folder
    res = client.post("/api/system/browse-folder", json={"initial_dir": "inbox"})
    assert res.status_code == 200
    assert res.json()["path"] == "C:/Mock/Path"

    # 5. Glossary GET
    res = client.get("/api/glossary?course=Discrete%20Mathematics")
    assert res.status_code == 200
    assert "terms" in res.json()

    # 6. Glossary upload PDF / text
    sample_text = b"Dijkstra's Algorithm (\xe6\x88\xb4\xe5\x85\x8b\xe6\x96\xaf\xe7\x89\xb9\xe6\x8b\x89) is a shortest path algorithm."
    res = client.post(
        "/api/glossary/upload-pdf",
        files={"file": ("syllabus.txt", io.BytesIO(sample_text), "text/plain")},
        data={"course": "Discrete Mathematics"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert len(res.json()["terms"]) >= 1


# ===========================================================================
# 9. Lecture & Glossary Management CRUD & Mode Switching Tests
# ===========================================================================

def test_lecture_management_crud(isolated_gui_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify lecture renaming, folder opening, alias endpoint, and safe deletion."""
    client = TestClient(app)

    # Setup dummy lecture bundle in exports/
    lecture_dir = isolated_gui_env / "exports" / "OperatingSystems" / "20261017"
    lecture_dir.mkdir(parents=True, exist_ok=True)
    (lecture_dir / "03_summary.md").write_text("# 【課堂考前重點總結筆記】作業系統 (2026-10-17)", encoding="utf-8")
    (lecture_dir / "02_cleaned.txt").write_text("課堂文字內容", encoding="utf-8")
    (lecture_dir / "run.json").write_text('{"course": "OperatingSystems", "title": "20261017_OS"}', encoding="utf-8")

    # 1. Alias endpoint GET /api/lecture/{lecture_id}
    res = client.get("/api/lecture/OperatingSystems_20261017")
    assert res.status_code == 200
    assert res.json()["course"] == "OperatingSystems"

    # 2. Rename lecture
    res = client.post(
        "/api/lecture/OperatingSystems_20261017/rename",
        json={"course": "AdvancedOS", "title": "期中複習"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 3. Open folder in desktop (mocked)
    monkeypatch.setattr("lecture_pipeline.gui.server.open_folder_in_desktop", lambda p: True)
    res = client.post("/api/lecture/OperatingSystems_20261017/open-folder")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 4. Delete lecture (safely deletes from exports/)
    res = client.delete("/api/lecture/OperatingSystems_20261017")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert not lecture_dir.exists()

    # 5. Safety guard: attempting to delete an item outside exports is blocked
    from lecture_pipeline.gui.bridge import _lecture_cache, delete_lecture
    outside_dir = isolated_gui_env / "archive" / "SecretAudio"
    outside_dir.mkdir(parents=True, exist_ok=True)
    _lecture_cache["SecretAudio"] = outside_dir
    safe_res = delete_lecture("SecretAudio")
    assert safe_res["status"] == "error"
    assert "安全防護限制" in safe_res["message"]
    assert outside_dir.exists()  # Kept completely safe!


def test_glossary_management_crud(isolated_gui_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify complete CRUD lifecycle of courses and domain terms."""
    client = TestClient(app)

    # Mock browse-file
    monkeypatch.setattr("lecture_pipeline.gui.server.browse_file_dialog", lambda p: "C:/Mock/File.pdf")
    res = client.post("/api/system/browse-file", json={})
    assert res.status_code == 200
    assert res.json()["path"] == "C:/Mock/File.pdf"

    # 1. Add course
    res = client.post("/api/glossary/course", json={"course": "NetworkSecurity"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert "NetworkSecurity" in res.json()["courses"]

    # 2. Add term
    res = client.post(
        "/api/glossary/term",
        json={"course": "NetworkSecurity", "term": "RSA", "translation": "非對稱加密"},
    )
    assert res.status_code == 200
    terms = [t["term"] for t in res.json()["terms"]]
    assert "RSA" in terms

    # 3. Edit term
    res = client.put(
        "/api/glossary/term",
        json={"course": "NetworkSecurity", "old_term": "RSA", "new_term": "RSA Algorithm", "new_translation": "RSA 密碼演算法"},
    )
    assert res.status_code == 200
    terms = [t["term"] for t in res.json()["terms"]]
    assert "RSA Algorithm" in terms

    # 4. Delete term
    res = client.request(
        "DELETE",
        "/api/glossary/term",
        json={"course": "NetworkSecurity", "term": "RSA Algorithm"},
    )
    assert res.status_code == 200
    terms = [t["term"] for t in res.json()["terms"]]
    assert "RSA Algorithm" not in terms

    # 5. Delete course
    res = client.delete("/api/glossary/course/NetworkSecurity")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_mode_switching_actions(isolated_gui_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify /api/mode/switch triggering window show/hide and browser opening."""
    client = TestClient(app)

    # Mock webbrowser.open and window show/hide
    monkeypatch.setattr("webbrowser.open", lambda u: True)
    monkeypatch.setattr("lecture_pipeline.gui.window.show_desktop_window", lambda: True)
    monkeypatch.setattr("lecture_pipeline.gui.window.hide_desktop_window", lambda: True)

    # 1. Switch to web
    res = client.post("/api/mode/switch", json={"mode": "web"})
    assert res.status_code == 200
    assert res.json()["mode"] == "web"

    # 2. Switch to window
    res = client.post("/api/mode/switch", json={"mode": "window"})
    assert res.status_code == 200
    assert res.json()["mode"] == "window"

    # 3. Switch to both
    res = client.post("/api/mode/switch", json={"mode": "both"})
    assert res.status_code == 200
    assert res.json()["mode"] == "both"


def test_glossary_empty_state(isolated_gui_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when all courses are deleted, glossary returns empty course, [] courses, [] terms without fallback to Discrete Mathematics."""
    client = TestClient(app)

    # Mock get_available_courses to return empty list
    monkeypatch.setattr("lecture_pipeline.gui.server.get_available_courses", lambda: [])
    monkeypatch.setattr("lecture_pipeline.gui.bridge.get_available_courses", lambda: [])

    res = client.get("/api/glossary")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["course"] == ""
    assert data["courses"] == []
    assert data["terms"] == []
    assert "Discrete Mathematics" not in str(data)


def test_task_progress_and_completion_broadcast(isolated_gui_env: Path) -> None:
    """Verify task progress reporting, task completion notification, and error notification."""
    from lecture_pipeline.gui.bridge import (
        update_task_progress,
        notify_task_completed,
        notify_task_error,
        get_current_task_status,
    )

    # 1. Update progress
    update_task_progress(
        stage="音訊前處理",
        progress=15,
        detail="標準化音訊...",
        task_name="演算法概論__20261017",
        running=True,
    )
    status = get_current_task_status()
    assert status["running"] is True
    assert status["progress"] == 15
    assert status["stage"] == "音訊前處理"
    assert status["task_name"] == "演算法概論__20261017"

    # 2. Complete task
    notify_task_completed("演算法概論__20261017", detail="處理成功完成")
    status = get_current_task_status()
    assert status["running"] is False
    assert status["progress"] == 100

    # 3. Task error
    notify_task_error("測試錯誤訊息", task_name="演算法概論__20261017")
    status = get_current_task_status()
    assert status["running"] is False
    assert status["stage"] == "處理失敗"
    assert "測試錯誤訊息" in status["detail"]


def test_tray_backend_ensure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify tray._ensure_backend_running checks port status before attempting startup."""
    from lecture_pipeline.automation.tray import WatcherTray

    tray = WatcherTray()
    # Mock is_port_in_use to return True (already running)
    called = []
    monkeypatch.setattr("lecture_pipeline.gui.launcher.is_port_in_use", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr("lecture_pipeline.gui.launcher.run_uvicorn_server", lambda h, p: called.append(p))

    tray._ensure_backend_running()
    assert len(called) == 0  # Should not attempt to re-bind


def test_resolve_export_lecture_file_and_open(isolated_gui_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that opening a lecture file resolves to the exports/ directory with fallback creation."""
    client = TestClient(app)
    work_dir = isolated_gui_env / "work"
    lecture_dir = work_dir / "261017_資料結構"
    lecture_dir.mkdir(parents=True, exist_ok=True)
    (lecture_dir / "03_summary.md").write_text("# 資料結構考前重點總結", encoding="utf-8")

    # 1. Test direct resolver function
    resolved = resolve_export_lecture_file("261017_資料結構", file_type="summary")
    assert resolved is not None
    assert resolved.is_file()
    assert "exports" in str(resolved).lower()
    assert "資料結構" in resolved.name or "資料結構" in str(resolved.parent)
    assert "# 資料結構考前重點總結" in resolved.read_text(encoding="utf-8")

    # 2. Test /api/system/open-file endpoint
    # Set LECTURE_PIPELINE_TEST_MODE to 1 so os.startfile is mocked
    monkeypatch.setenv("LECTURE_PIPELINE_TEST_MODE", "1")
    res = client.post("/api/system/open-file", json={"lecture_id": "261017_資料結構", "file_type": "summary"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "exports" in data["path"].lower()


def test_index_html_player_empty_state_and_button_text(isolated_gui_env: Path) -> None:
    """Verify that index.html contains empty default states without demo placeholders and updated button label."""
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Button text updated to 本機開啟檔案
    assert 'id="ui-player-open-note-btn">本機開啟檔案<' in html
    # No DEMO placeholder text in panes
    assert "請先從上方選取一堂課堂。" not in html
    # Empty default panes and titles
    assert 'id="history-transcript-pane" class="space-y-2 flex-1 overflow-y-auto pr-1"></div>' in html
    assert 'id="history-summary-pane" class="space-y-3 flex-1 overflow-y-auto pr-1"></div>' in html
    assert 'id="player-lecture-title" class="text-slate-100 dark:text-slate-100 light:text-slate-900"></span>' in html
    assert 'id="player-lecture-meta" class="text-xs text-slate-400 mt-0.5"></p>' in html


