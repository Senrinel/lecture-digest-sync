"""Tests for clean-device installation, onboarding, self-healing, and fallback paths."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from lecture_pipeline.automation import autostart
from lecture_pipeline.automation.watcher import (
    DeadLetterQuarantine,
    is_missing_environment_dependency_error,
)
from lecture_pipeline.gui.server import app
from lecture_pipeline.utils import bootstrap, process as proc_utils


def test_api_key_placeholder_filtering():
    """Verify template placeholders and malformed values are rejected as valid API keys."""
    assert bootstrap.is_valid_api_key_value(None) is False
    assert bootstrap.is_valid_api_key_value("") is False
    assert bootstrap.is_valid_api_key_value("   ") is False
    assert bootstrap.is_valid_api_key_value("AIzaSy_YOUR_ACTUAL_GEMINI_API_KEY_HERE") is False
    assert bootstrap.is_valid_api_key_value("your_api_key_here") is False
    assert bootstrap.is_valid_api_key_value("short") is False
    assert bootstrap.is_valid_api_key_value("AIzaSyRealDemoKeyValue1234567890") is True


def test_is_foreign_user_path_detection():
    """Verify Windows user-profile paths belonging to a different username are flagged."""
    current_user = Path.home().name
    other_user = "AnotherCleanUserXYZ" if current_user.lower() != "anothercleanuserxyz" else "DifferentUserABC"

    assert bootstrap.is_foreign_user_path(rf"C:\Users\{other_user}\Pictures\Record") is True
    assert bootstrap.is_foreign_user_path(rf"C:\Users\{current_user}\Documents\課堂錄音\收件匣") is False
    assert bootstrap.is_foreign_user_path("inbox") is False
    assert bootstrap.is_foreign_user_path("") is False


def test_repair_and_init_on_completely_clean_directory(tmp_path, monkeypatch):
    """Verify empty clean directory initializes config, folders, and WebView2 fallback."""
    fake_home = tmp_path / "clean_user_home"
    fake_home.mkdir()
    app_dir = tmp_path / "clean_app"
    app_dir.mkdir()

    monkeypatch.chdir(app_dir)
    monkeypatch.setattr(bootstrap, "get_app_base_dir", lambda: app_dir)
    monkeypatch.setattr(bootstrap, "is_webview2_available", lambda: False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    res = bootstrap.repair_and_init_environment()
    assert res["status"] == "ok"

    cfg_file = Path(res["config_file"])
    assert cfg_file.is_file()
    cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))

    assert cfg["gui"]["mode"] == "web"
    for key in ("inbox_dir", "exports_dir", "courses_dir", "record_archive_dir"):
        p = Path(cfg["paths"][key])
        assert p.is_dir(), f"Expected directory to be created for {key}: {p}"
    assert cfg["export"]["export_dir"] == cfg["paths"]["exports_dir"]


def test_repair_recovers_corrupt_yaml_json_and_foreign_paths(tmp_path, monkeypatch):
    """Verify corrupted config/state files are backed up and rebuilt automatically."""
    fake_home = tmp_path / "target_home"
    fake_home.mkdir()
    app_dir = tmp_path / "broken_app"
    app_dir.mkdir()

    monkeypatch.chdir(app_dir)
    monkeypatch.setattr(bootstrap, "get_app_base_dir", lambda: app_dir)
    monkeypatch.setattr(bootstrap, "is_webview2_available", lambda: True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    # 1. Broken YAML config
    cfg_path = app_dir / "config.yaml"
    cfg_path.write_text("paths:\n  inbox_dir: [unterminated\n", encoding="utf-8")

    # 2. Broken JSON files + stale .lock
    (app_dir / "pending_uploads").mkdir()
    (app_dir / "pending_uploads" / "queue.json").write_text("{corrupt", encoding="utf-8")
    (app_dir / "quota_lock.json").write_text("{corrupt", encoding="utf-8")
    (app_dir / "token.json").write_text("not-json", encoding="utf-8")
    (app_dir / "stale.lock").write_text("1234", encoding="utf-8")

    res = bootstrap.repair_and_init_environment()
    assert res["status"] == "ok"
    assert len(res["repairs"]) >= 4
    assert (app_dir / "config.yaml.corrupt.bak").is_file()
    assert (app_dir / "pending_uploads" / "queue.json.corrupt.bak").is_file()
    assert (app_dir / "quota_lock.json.corrupt.bak").is_file()
    assert (app_dir / "token.json.corrupt.bak").is_file()
    assert not (app_dir / "token.json").is_file()
    assert not (app_dir / "quota_lock.json").is_file()
    assert json.loads((app_dir / "pending_uploads" / "queue.json").read_text(encoding="utf-8")) == []
    assert not (app_dir / "stale.lock").is_file()

    # 3. Foreign user path inside otherwise valid config gets sanitized cleanly
    foreign_cfg = bootstrap.get_default_config_dict()
    foreign_cfg["paths"]["record_archive_dir"] = r"C:\Users\ForeignDev999\Pictures\Record"
    cfg_path.write_text(yaml.safe_dump(foreign_cfg, allow_unicode=True), encoding="utf-8")

    res2 = bootstrap.repair_and_init_environment()
    assert res2["status"] == "ok"
    repaired_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "ForeignDev999" not in repaired_cfg["paths"]["record_archive_dir"]
    assert str(fake_home) in repaired_cfg["paths"]["record_archive_dir"]


def test_repair_preserves_valid_user_files(tmp_path, monkeypatch):
    """Verify re-running repair does not overwrite valid user token or custom folders."""
    app_dir = tmp_path / "existing_app"
    app_dir.mkdir()
    custom_archive = app_dir / "my_custom_archive"
    custom_inbox = app_dir / "my_inbox"
    custom_exports = app_dir / "my_exports"
    custom_courses = app_dir / "my_courses"

    monkeypatch.chdir(app_dir)
    monkeypatch.setattr(bootstrap, "get_app_base_dir", lambda: app_dir)
    monkeypatch.setattr(bootstrap, "is_webview2_available", lambda: True)

    cfg = bootstrap.get_default_config_dict()
    cfg["paths"]["inbox_dir"] = str(custom_inbox)
    cfg["paths"]["exports_dir"] = str(custom_exports)
    cfg["paths"]["courses_dir"] = str(custom_courses)
    cfg["paths"]["record_archive_dir"] = str(custom_archive)
    cfg["export"]["export_dir"] = str(custom_exports)
    (app_dir / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    token_data = {"token": "valid-refreshable-token", "refresh_token": "r-tok"}
    (app_dir / "token.json").write_text(json.dumps(token_data), encoding="utf-8")

    res = bootstrap.repair_and_init_environment()
    assert res["status"] == "ok"
    assert res["paths"]["record_archive_dir"] == str(custom_archive)
    assert custom_archive.is_dir()
    assert json.loads((app_dir / "token.json").read_text(encoding="utf-8")) == token_data


def test_save_gemini_api_key_and_credentials_json(tmp_path, monkeypatch):
    """Verify API key validation, .env persistence, and OAuth credentials validation."""
    env_file = tmp_path / ".env"
    env_file.write_text('OTHER_FLAG="keep_me"\nGEMINI_API_KEY="old"\n', encoding="utf-8")

    ok_bad, _ = bootstrap.save_gemini_api_key("AIzaSy_YOUR_ACTUAL_GEMINI_API_KEY_HERE", verify=False, env_path=env_file)
    assert ok_bad is False
    assert 'GEMINI_API_KEY="old"' in env_file.read_text(encoding="utf-8")

    monkeypatch.setenv("GEMINI_API_KEY", "old")
    ok_good, _ = bootstrap.save_gemini_api_key("AIzaSyValidUnitTestKey123456789", verify=False, env_path=env_file)
    assert ok_good is True
    env_text = env_file.read_text(encoding="utf-8")
    assert 'OTHER_FLAG="keep_me"' in env_text
    assert 'GEMINI_API_KEY="AIzaSyValidUnitTestKey123456789"' in env_text

    # OAuth credentials validation
    creds_file = tmp_path / "credentials.json"
    ok_json_bad, _ = bootstrap.save_google_credentials_json(b"{not json", creds_path=creds_file)
    assert ok_json_bad is False
    ok_schema_bad, _ = bootstrap.save_google_credentials_json(b'{"hello": "world"}', creds_path=creds_file)
    assert ok_schema_bad is False

    valid_oauth = {"installed": {"client_id": "cid-123", "client_secret": "sec-456"}}
    bom_bytes = b"\xef\xbb\xbf" + json.dumps(valid_oauth).encode("utf-8")
    ok_oauth, _ = bootstrap.save_google_credentials_json(bom_bytes, creds_path=creds_file)
    assert ok_oauth is True
    assert json.loads(creds_file.read_text(encoding="utf-8"))["installed"]["client_id"] == "cid-123"


def test_resolve_binary_portable_bin_and_install_ffmpeg(tmp_path, monkeypatch):
    """Verify portable bin/ takes priority and install_ffmpeg_locally extracts binaries without admin."""
    app_dir = tmp_path / "portable_app"
    bin_dir = app_dir / "bin"
    app_dir.mkdir()

    # Build synthetic zip payload in memory containing ffmpeg.exe & ffprobe.exe
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("ffmpeg-test-build/bin/ffmpeg.exe", b"MZ" + b"\x00" * 64)
        zf.writestr("ffmpeg-test-build/bin/ffprobe.exe", b"MZ" + b"\x00" * 64)
    zip_bytes = zip_buf.getvalue()

    class _FakeResp:
        def __init__(self, data: bytes):
            self._stream = io.BytesIO(data)
            self.headers = {"Content-Length": str(len(data))}

        def read(self, n: int = -1) -> bytes:
            return self._stream.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _FakeResp(zip_bytes))
    ok, msg = bootstrap.install_ffmpeg_locally(target_bin_dir=bin_dir)
    assert ok is True, msg
    assert (bin_dir / "ffmpeg.exe").is_file()
    assert (bin_dir / "ffprobe.exe").is_file()

    # Verify resolve_binary finds app_dir / "bin" / "ffmpeg.exe" first
    monkeypatch.chdir(app_dir)
    monkeypatch.setattr(sys, "executable", str(app_dir / "LecturePipeline.exe"))
    resolved = proc_utils.resolve_binary("ffmpeg")
    assert Path(resolved).resolve() == (bin_dir / "ffmpeg.exe").resolve()


def test_autostart_frozen_shortcut_and_legacy_cleanup(tmp_path, monkeypatch):
    """Verify frozen .exe autostart uses Startup .lnk and cleans legacy .vbs."""
    startup_dir = tmp_path / "Startup"
    startup_dir.mkdir()
    exe_file = tmp_path / "LecturePipeline.exe"
    exe_file.write_bytes(b"")
    legacy_vbs = startup_dir / "LecturePipelineWatcher.vbs"
    legacy_vbs.write_text("legacy", encoding="utf-8")

    monkeypatch.setattr(autostart, "get_startup_dir", lambda: startup_dir)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_file))

    created = autostart.enable_autostart()
    assert created.suffix == ".lnk"
    assert created.is_file()
    assert not legacy_vbs.exists()
    assert autostart.get_autostart_status()["enabled"] is True

    assert autostart.disable_autostart() is True
    assert not created.exists()
    assert autostart.get_autostart_status()["enabled"] is False


def test_webview2_unavailable_graceful_fallback(monkeypatch):
    """Verify missing WebView2 Runtime cleanly redirects GUI startup to browser mode."""
    from lecture_pipeline.gui import launcher, window

    opened_urls: list[str] = []
    monkeypatch.setattr(bootstrap, "is_webview2_available", lambda: False)
    monkeypatch.setattr("webbrowser.open", lambda url: opened_urls.append(url) or True)

    with patch("lecture_pipeline.gui.window.launch_window_process") as mock_lwp:
        assert window.show_desktop_window(host="127.0.0.1", port=8765) is True
        mock_lwp.assert_not_called()
        assert opened_urls == ["http://127.0.0.1:8765"]

    opened_urls.clear()
    with patch("lecture_pipeline.gui.launcher.save_remembered_gui_mode", side_effect=lambda m: m), \
         patch("lecture_pipeline.gui.launcher.run_uvicorn_server"), \
         patch("lecture_pipeline.gui.launcher.is_port_in_use", return_value=False), \
         patch("lecture_pipeline.gui.launcher.start_memory_watchdog"), \
         patch("lecture_pipeline.gui.launcher.time.sleep", side_effect=[None, KeyboardInterrupt()]), \
         patch("lecture_pipeline.gui.window.show_desktop_window") as mock_show, \
         patch("lecture_pipeline.gui.window.close_external_desktop_window"), \
         patch("lecture_pipeline.automation.watcher.watch_inbox"), \
         patch("lecture_pipeline.gui.launcher.WatcherTray") as mock_tray:
        mock_tray.return_value.start.return_value = None
        launcher.launch_gui(mode="window", host="127.0.0.1", port=8765)
        mock_show.assert_not_called()
        assert opened_urls == ["http://127.0.0.1:8765"]


def test_watcher_missing_dependency_exempt_from_quarantine(tmp_path):
    """Verify missing API key / FFmpeg / local backend never throws valid audio into failed/."""
    assert is_missing_environment_dependency_error(RuntimeError("未設定 GEMINI_API_KEY 環境變數")) is True
    assert is_missing_environment_dependency_error(FileNotFoundError("找不到 ffmpeg 執行檔")) is True
    assert is_missing_environment_dependency_error(RuntimeError("C7 本機後端尚未安裝")) is True
    assert is_missing_environment_dependency_error(RuntimeError("Corrupt audio stream invalid header")) is False

    quarantine = DeadLetterQuarantine(tmp_path)
    audio = tmp_path / "lecture_01.m4a"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)

    # Trigger 5 times with missing dependency error -> must stay in inbox (never quarantined)
    for _ in range(5):
        quarantined = quarantine.record_failure(
            [audio],
            RuntimeError("未設定 GEMINI_API_KEY 環境變數"),
        )
        assert quarantined is False
    assert audio.is_file()
    assert not (tmp_path / "failed").exists()


def test_rest_api_setup_endpoints_and_ui_sanity():
    """Verify /api/setup/* routes, /api/status telemetry keys, and clean index.html UI."""
    client = TestClient(app)

    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "LinKJ" not in res_html.text
    assert "RTX 4060 8GB" not in res_html.text
    assert "onboarding-banner" in res_html.text
    assert "setup-wizard-modal" in res_html.text
    assert "enableWindowsStartup()" in res_html.text
    assert "refreshSystemDiagnostics()" in res_html.text

    res_status = client.get("/api/status")
    assert res_status.status_code == 200
    st = res_status.json()
    for field in ("os_info", "ffmpeg", "webview2", "local_stt", "api_key", "google_docs", "autostart", "pending_uploads", "needs_onboarding"):
        assert field in st

    with patch("lecture_pipeline.gui.server.setup_gemini_api_key_action", return_value={"status": "ok", "message": "saved"}) as m_key:
        r1 = client.post("/api/setup/gemini-key", json={"api_key": "AIzaSyTestKey", "verify": True})
        assert r1.status_code == 200
        assert r1.json()["status"] == "ok"
        m_key.assert_called_once_with(api_key="AIzaSyTestKey", verify=True)

    with patch("lecture_pipeline.gui.server.setup_credentials_action", return_value={"status": "ok"}) as m_creds:
        payload = b'{"installed":{"client_id":"c","client_secret":"s"}}'
        r2a = client.post("/api/setup/credentials", files={"file": ("credentials.json", payload, "application/json")})
        assert r2a.status_code == 200
        assert m_creds.call_args[0][0] == payload

        m_creds.reset_mock()
        r2b = client.post("/api/setup/credentials", content=payload, headers={"Content-Type": "application/json"})
        assert r2b.status_code == 200
        assert m_creds.call_args[0][0] == payload

    with patch("lecture_pipeline.gui.server.install_ffmpeg_action", return_value={"status": "started"}) as m_ff:
        assert client.post("/api/setup/install-ffmpeg").json()["status"] == "started"
        m_ff.assert_called_once()

    with patch("lecture_pipeline.gui.server.repair_environment_action", return_value={"status": "ok", "repairs": []}) as m_rep:
        assert client.post("/api/setup/repair").json()["status"] == "ok"
        m_rep.assert_called_once()

    with patch("lecture_pipeline.gui.server.toggle_autostart_action", return_value={"status": "ok", "enabled": True}) as m_auto:
        assert client.post("/api/setup/autostart").json()["enabled"] is True
        m_auto.assert_called_once_with(enabled=None)
        m_auto.reset_mock()
        assert client.post("/api/setup/autostart", json={"enabled": False}).json()["status"] == "ok"
        m_auto.assert_called_once_with(enabled=False)
