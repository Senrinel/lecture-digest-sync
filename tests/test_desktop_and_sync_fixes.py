"""Regression tests for desktop actions, single-instance guard, partial STT recovery, and Drive link isolation."""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

import main as app_main
from lecture_pipeline.automation.watcher import run_pipeline_for_group
from lecture_pipeline.cleanup.base import CleanupResult
from lecture_pipeline.export.drive_publisher import (
    GoogleDrivePublisher,
    _escape_drive_query,
)
from lecture_pipeline.export.notebook_bundle import prepare_notebook_bundle
from lecture_pipeline.gui import bridge
from lecture_pipeline.gui.server import app
from lecture_pipeline.naming import parse_filename
from lecture_pipeline.state import PreprocessOptions, RunState, SegmentationOptions, save_run_state
from lecture_pipeline.stt.base import ChunkTranscription, FullTranscription
from lecture_pipeline.summary.base import SummaryResult


def test_api_ping_endpoint() -> None:
    """Verify lightweight /api/ping responds cleanly with pid."""
    client = TestClient(app)
    res = client.get("/api/ping")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert isinstance(data["pid"], int)


def test_single_instance_mutex_and_ping_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Windows Named Mutex rejects duplicate instances and checks /api/ping first."""
    if sys.platform != "win32":
        pytest.skip("Windows Named Mutex test only applies on win32")

    closed_handles = []

    class FakeKernel32:
        def __init__(self, last_err: int, handle: int = 1234):
            self._err = last_err
            self._handle = handle

        def CreateMutexW(self, sec, initial_owner, name):
            return self._handle

        def CloseHandle(self, h):
            closed_handles.append(h)
            return 1

    # 1. Simulate ERROR_ALREADY_EXISTS (183)
    fake_k32_dup = FakeKernel32(last_err=183, handle=777)
    import ctypes

    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake_k32_dup)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 183)
    assert app_main._acquire_single_instance_mutex() is False
    assert 777 in closed_handles

    # 2. Simulate primary owner (last_error == 0)
    fake_k32_ok = FakeKernel32(last_err=0, handle=888)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake_k32_ok)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0)
    assert app_main._acquire_single_instance_mutex() is True
    assert app_main._app_mutex_handle == 888

    # 3. Verify check_and_forward_to_existing_instance probes /api/ping first
    requested_urls = []

    class FakeResp:
        def __init__(self, status=200):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        requested_urls.append((req.full_url, timeout))
        return FakeResp(200)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ok = app_main.check_and_forward_to_existing_instance([])
    assert ok is True
    assert requested_urls[0][0].endswith("/api/ping")
    assert requested_urls[0][1] >= 2.0
    assert any(u.endswith("/api/window/show") for u, _ in requested_urls)


def test_desktop_open_functions_and_dialogs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Code.exe launch, Explorer relative path resolution, and PowerShell -STA dialogs."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("gui:\n  mode: window\n", encoding="utf-8")
    monkeypatch.setattr(bridge, "CONFIG_FILE_PATH", cfg_path)
    monkeypatch.delenv("LECTURE_PIPELINE_TEST_MODE", raising=False)

    # 1. open_file_in_vscode_preview uses direct Code.exe without shell=True and strips PyInstaller env
    fake_code = tmp_path / "Microsoft VS Code" / "Code.exe"
    fake_code.parent.mkdir(parents=True, exist_ok=True)
    fake_code.write_bytes(b"MZ")

    note_dir = tmp_path / "筆記 測試"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_file = note_dir / "01_課堂筆記.md"
    note_file.write_text("# 筆記", encoding="utf-8")

    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return SimpleNamespace(pid=9999)

    fake_internal = tmp_path / "_internal"
    fake_internal.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("_MEIPASS", str(fake_internal))
    monkeypatch.setenv("PYTHONHOME", r"C:\FakePython")
    monkeypatch.setenv("PATH", f"{fake_internal}{os.pathsep}{fake_internal / 'pywin32_system32'}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_internal), raising=False)
    monkeypatch.setattr(bridge, "_resolve_vscode_executable", lambda: str(fake_code))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert bridge.open_file_in_vscode_preview(note_file) is True
    assert len(popen_calls) == 1
    cmd, kw = popen_calls[0]
    assert cmd == [str(fake_code), "-r", str(note_file.resolve())]
    assert kw.get("shell") is False
    assert kw.get("stdin") == subprocess.DEVNULL
    assert kw.get("stdout") == subprocess.DEVNULL
    assert kw.get("stderr") == subprocess.DEVNULL
    assert kw.get("close_fds") is True
    assert "_MEIPASS" not in kw["env"]
    assert "PYTHONHOME" not in kw["env"]
    child_path_entries = [
        p.lower()
        for k, v in kw["env"].items()
        if k.upper() == "PATH"
        for p in v.split(os.pathsep)
    ]
    assert str(fake_internal).lower() not in child_path_entries
    assert str(fake_internal / "pywin32_system32").lower() not in child_path_entries

    # Fallback to desktop open when VS Code is absent
    popen_calls.clear()
    monkeypatch.setattr(bridge, "_resolve_vscode_executable", lambda: None)
    monkeypatch.setattr(bridge, "open_file_in_desktop", lambda p: True)
    assert bridge.open_file_in_vscode_preview(note_file) is True

    # 2. open_folder_in_desktop resolves relative paths against CONFIG_FILE_PATH.parent
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    assert bridge.open_folder_in_desktop("logs") is True
    expected_logs = (tmp_path / "logs").resolve()
    assert expected_logs.is_dir()
    if sys.platform == "win32":
        assert popen_calls[0][0] == ["explorer.exe", str(expected_logs)]

    # 3. browse_folder_dialog & browse_file_dialog PowerShell fallback uses -STA & UTF-8
    real_import = builtins.__import__

    def no_tk_import(name, *args, **kwargs):
        if name == "tkinter" or name.startswith("tkinter."):
            raise ImportError("tkinter disabled for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_tk_import)
    silent_calls = []

    def fake_silent_run(cmd, **kwargs):
        silent_calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(expected_logs))

    monkeypatch.setattr(bridge, "silent_run", fake_silent_run)
    selected_dir = bridge.browse_folder_dialog("logs")
    assert selected_dir == str(expected_logs)
    ps_cmd = silent_calls[0][0]
    assert "-STA" in ps_cmd
    assert "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;" in ps_cmd[-1]
    assert "$owner.TopMost = $true;" in ps_cmd[-1]
    assert silent_calls[0][1]["env"].get("LP_BROWSE_INIT_DIR") == str(expected_logs)

    silent_calls.clear()
    selected_file = bridge.browse_file_dialog("logs")
    assert selected_file == str(expected_logs)
    assert "-STA" in silent_calls[0][0]
    assert "OpenFileDialog" in silent_calls[0][0][-1]


def test_resolve_export_lecture_file_respects_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify multi-date course files never resolve across dates."""
    cfg_path = tmp_path / "config.yaml"
    export_dir = tmp_path / "exports"
    d_old = export_dir / "世界文明史" / "20261110"
    d_new = export_dir / "世界文明史" / "20261117"
    d_old.mkdir(parents=True)
    d_new.mkdir(parents=True)
    f_old = d_old / "01_世界文明史_20261110_重點總結筆記.md"
    f_new = d_new / "01_世界文明史_20261117_重點總結筆記.md"
    f_old.write_text("# Old Lecture", encoding="utf-8")
    f_new.write_text("# New Lecture", encoding="utf-8")

    cfg_path.write_text(
        yaml.safe_dump({"paths": {"work_dir": str(tmp_path / "work")}, "export": {"export_dir": str(export_dir)}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "CONFIG_FILE_PATH", cfg_path)
    bridge._lecture_cache.clear()

    resolved_old = bridge.resolve_export_lecture_file("世界文明史_20261110", "summary")
    assert resolved_old is not None
    assert resolved_old.resolve() == f_old.resolve()


def test_drive_metadata_isolation_and_query_escaping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Drive query escaping, export run.json mirroring, and strict per-lecture docs_url isolation."""
    assert _escape_drive_query("O'Brien\\Course") == r"O\'Brien\\Course"

    cfg_path = tmp_path / "config.yaml"
    work_dir = tmp_path / "work"
    export_dir = tmp_path / "exports"
    logs_dir = tmp_path / "logs"
    for d in (work_dir, export_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "paths": {"work_dir": str(work_dir), "archive_dir": str(tmp_path / "archive")},
                "export": {"export_dir": str(export_dir)},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "CONFIG_FILE_PATH", cfg_path)
    bridge._lecture_cache.clear()
    bridge._drive_log_cache.update({"path": "", "mtime": -1.0, "entries": []})

    # Lecture A: Exported AND Drive published -> exports/run.json contains Drive doc link
    work_a = work_dir / "Course'A" / "20261119"
    work_a.mkdir(parents=True)
    (work_a / "02_cleaned.txt").write_text("Course A cleaned content body.", encoding="utf-8")
    (work_a / "03_summary.md").write_text("# Course A Summary", encoding="utf-8")
    save_run_state(
        RunState(
            source_filename="a.mp3",
            source_path=str(work_a / "a.mp3"),
            course="Course'A",
            date="20261119",
            pre_options=PreprocessOptions(),
            seg_options=SegmentationOptions(),
            metadata={"stt_channel": "C1"},
        ),
        work_a,
    )
    prepare_notebook_bundle(work_a, export_dir=export_dir)

    token_file = tmp_path / "token.json"
    token_file.write_text("{}", encoding="utf-8")
    mock_service = MagicMock()
    mock_service.files().list().execute.side_effect = [
        {"files": [{"id": "root_f", "name": "LectureNotes"}]},
        {"files": [{"id": "course_a_f", "name": "Course'A"}]},
        {"files": []},
        {"files": []},
    ]
    mock_service.files().create().execute.side_effect = [
        {"id": "doc_a_sum", "name": "sum", "webViewLink": "https://docs.google.com/document/d/doc_a_sum/edit"},
        {"id": "doc_a_clean", "name": "clean", "webViewLink": "https://docs.google.com/document/d/doc_a_clean/edit"},
    ]

    with patch("lecture_pipeline.export.drive_publisher.build", return_value=mock_service), \
         patch("lecture_pipeline.export.drive_publisher.Credentials"):
        pub = GoogleDrivePublisher(credentials_file=tmp_path / "creds.json", token_file=token_file)
        pub.publish_lecture(work_a)

    # Verify query escaped single quote in Course'A
    list_queries = [c.kwargs.get("q", "") for c in mock_service.files().list.call_args_list if "q" in c.kwargs]
    assert any(r"Course\'A" in q for q in list_queries)

    exp_run_a = export_dir / "Course'A" / "20261119" / "run.json"
    assert exp_run_a.is_file()
    exp_data_a = json.loads(exp_run_a.read_text(encoding="utf-8"))
    assert exp_data_a["docs_url"] == "https://docs.google.com/document/d/doc_a_sum/edit"
    assert exp_data_a["drive_link"] == "https://drive.google.com/drive/folders/course_a_f"

    # Lecture B: No exports/run.json (pre-fix exported bundle), only work/.../run.json has its Drive link
    exp_b = export_dir / "電磁學" / "20261113"
    exp_b.mkdir(parents=True)
    (exp_b / "01_電磁學_20261113_重點總結筆記.md").write_text("# 電磁學", encoding="utf-8")
    (exp_b / "02_電磁學_20261113_完整課堂整理文稿.md").write_text("馬克士威方程式推導內容", encoding="utf-8")
    work_b = work_dir / "電磁學" / "20261113"
    work_b.mkdir(parents=True)
    save_run_state(
        RunState(
            source_filename="em.mp3",
            source_path=str(work_b / "em.mp3"),
            course="電磁學",
            date="20261113",
            pre_options=PreprocessOptions(),
            seg_options=SegmentationOptions(),
            metadata={
                "drive_course_folder_link": "https://drive.google.com/drive/folders/em_folder_1",
                "drive_summary_doc_link": "https://docs.google.com/document/d/em_doc_1/edit",
                "export_mirrored_dir": str(exp_b.resolve()),
            },
        ),
        work_b,
    )

    # Lecture C: Never uploaded anywhere, with only empty boilerplate header in export bundle;
    # watcher.log has Course A & untagged legacy Drive lines (must NOT leak to Lecture C!)
    exp_c = export_dir / "線性代數" / "20261118"
    exp_c.mkdir(parents=True)
    (exp_c / "01_線性代數_20261118_重點總結筆記.md").write_text("# 【課堂重點筆記】線性代數 (2026-11-18)\n\n> **課程名稱 (Course)**：線性代數\n\n---\n\n\n", encoding="utf-8")
    (exp_c / "02_線性代數_20261118_完整課堂整理文稿.md").write_text("# 【完整課堂整理文稿】線性代數 (2026-11-18)\n\n> **課程名稱 (Course)**：線性代數\n\n---\n\n\n", encoding="utf-8")

    (logs_dir / "watcher.log").write_text(
        "[2026-11-17 12:00:00] [INFO] 已同步至 Google Drive：https://drive.google.com/drive/folders/old_unrelated_folder\n"
        "[2026-11-19 20:00:00] [INFO] 已同步至 Google Drive [Course'A | 20261119]：https://drive.google.com/drive/folders/course_a_f\n",
        encoding="utf-8",
    )

    lectures = {item["course"]: item for item in bridge.get_archived_lectures()}
    assert lectures["Course'A"]["docs_url"] == "https://docs.google.com/document/d/doc_a_sum/edit"
    assert lectures["電磁學"]["docs_url"] == "https://docs.google.com/document/d/em_doc_1/edit"
    assert lectures["線性代數"]["docs_url"] is None
    assert lectures["線性代數"]["char_count"] == 0

    detail_b = bridge.get_lecture_content(lectures["電磁學"]["id"])
    detail_c = bridge.get_lecture_content(lectures["線性代數"]["id"])
    assert detail_b is not None and detail_b["docs_url"] == "https://docs.google.com/document/d/em_doc_1/edit"
    assert detail_c is not None and detail_c["docs_url"] is None


def test_watcher_rejects_partial_stt_checkpoint_and_logs_drive_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify partial 1/3 STT checkpoint triggers re-transcription and Drive errors are logged."""
    inbox_dir = tmp_path / "inbox"
    work_base = tmp_path / "work"
    archive_dir = tmp_path / "archive"
    export_dir = tmp_path / "exports"
    logs_dir = tmp_path / "logs"
    for d in (inbox_dir, work_base, archive_dir, export_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "paths": {"work_dir": str(work_base), "archive_dir": str(archive_dir)},
                "export": {"export_dir": str(export_dir)},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "CONFIG_FILE_PATH", cfg_path)
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")

    audio_file = inbox_dir / "演算法_20261119.mp3"
    audio_file.write_bytes(b"mp3")

    target_work = work_base / "演算法" / "20261119"
    chunks_dir = target_work / "chunks"
    chunks_dir.mkdir(parents=True)
    (target_work / "00_normalized.wav").write_bytes(b"RIFFdata")
    for idx in range(1, 4):
        (chunks_dir / f"chunk_00{idx}.wav").write_bytes(b"wav")

    # Seed incomplete 1-chunk checkpoint vs 3 VAD chunks
    partial_stt = FullTranscription(
        channel_name="C1",
        course="演算法",
        date="20261119",
        chunks=[ChunkTranscription(index=1, start_sec=0.0, end_sec=10.0, duration_sec=10.0, raw_text="點名", model_used="m", latency_sec=0.1)],
        full_text="點名",
        timestamped_text="[00:00] 點名",
        total_latency_sec=0.1,
    )
    (target_work / "01_raw.json").write_text(partial_stt.model_dump_json(), encoding="utf-8")

    full_stt = FullTranscription(
        channel_name="C1",
        course="演算法",
        date="20261119",
        chunks=[
            ChunkTranscription(index=i, start_sec=float((i - 1) * 10), end_sec=float(i * 10), duration_sec=10.0, raw_text=f"第{i}段", model_used="m", latency_sec=0.1)
            for i in range(1, 4)
        ],
        full_text="第一段\n第二段\n第三段完整課堂內容",
        timestamped_text="[00:00] 第一段",
        total_latency_sec=0.5,
    )
    mock_channel = MagicMock()
    mock_channel.transcribe_all.return_value = full_stt

    mock_cleaner = MagicMock()
    mock_cleaner.clean.return_value = CleanupResult(
        full_cleaned_text="第一段\n第二段\n第三段完整課堂內容",
        raw_char_count=16,
        cleaned_char_count=16,
        retention_ratio=1.0,
        model_used="m",
        prompt_template="videolingo_punct.md",
        total_latency_sec=0.1,
    )
    mock_summarizer = MagicMock()
    mock_summarizer.summarize.return_value = SummaryResult(
        full_summary_md="# 演算法重點",
        input_char_count=16,
        summary_char_count=6,
        model_used="m",
        prompt_template="fabric_summarize_lecture.md",
        total_latency_sec=0.1,
    )

    logged_events = []
    mock_pub = MagicMock()
    mock_pub.publish_lecture.side_effect = RuntimeError("Drive API quota boom")

    monkeypatch.setattr("lecture_pipeline.automation.watcher.parse_filename", lambda f: parse_filename(f, base_work_dir=work_base))
    monkeypatch.setattr("lecture_pipeline.automation.watcher.get_stt_channel", lambda cid: mock_channel)
    monkeypatch.setattr("lecture_pipeline.automation.watcher.GeminiTextCleanupEngine", lambda **kw: mock_cleaner)
    monkeypatch.setattr("lecture_pipeline.automation.watcher.GeminiLectureSummarizer", lambda **kw: mock_summarizer)
    monkeypatch.setattr("lecture_pipeline.automation.watcher.prune_and_archive", MagicMock())
    monkeypatch.setattr("lecture_pipeline.automation.watcher.is_internet_connected", lambda: True)
    monkeypatch.setattr("lecture_pipeline.automation.watcher.GoogleDrivePublisher", lambda **kw: mock_pub)
    monkeypatch.setattr("lecture_pipeline.automation.watcher.notify_processing_started", MagicMock())
    monkeypatch.setattr("lecture_pipeline.automation.watcher.notify_offline_pending", MagicMock())
    monkeypatch.setattr("lecture_pipeline.automation.watcher.log_watcher_event", lambda msg, level="INFO": logged_events.append((level, msg)))

    offline_q = MagicMock()
    out = run_pipeline_for_group(
        audio_files=[audio_file],
        channel_id="C1",
        archive_dir=archive_dir,
        export_dir=export_dir,
        upload_drive=True,
        offline_queue=offline_q,
    )
    assert out == target_work.resolve()
    assert mock_channel.transcribe_all.call_count == 1
    assert any(lvl == "WARNING" and "不完整的逐字稿斷點" in msg for lvl, msg in logged_events)
    assert any(lvl == "ERROR" and "Google Drive 同步失敗" in msg for lvl, msg in logged_events)
    assert offline_q.enqueue.call_count == 1
    assert (export_dir / "演算法" / "20261119" / "run.json").is_file()


def test_watcher_aborts_on_empty_stt_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify completely blank STT transcription raises an error before cleanup/export/upload."""
    work_base = tmp_path / "work"
    work_base.mkdir(parents=True)
    audio_file = tmp_path / "空音訊_20261119.mp3"
    audio_file.write_bytes(b"mp3")

    target_work = work_base / "空音訊" / "20261119"
    chunks_dir = target_work / "chunks"
    chunks_dir.mkdir(parents=True)
    (target_work / "00_normalized.wav").write_bytes(b"RIFFdata")
    (chunks_dir / "chunk_001.wav").write_bytes(b"wav")

    empty_stt = FullTranscription(
        channel_name="C1",
        course="空音訊",
        date="20261119",
        chunks=[ChunkTranscription(index=1, start_sec=0.0, end_sec=5.0, duration_sec=5.0, raw_text="", model_used="m", latency_sec=0.1)],
        full_text="   \n  ",
        timestamped_text="",
        total_latency_sec=0.1,
    )
    mock_channel = MagicMock()
    mock_channel.transcribe_all.return_value = empty_stt

    monkeypatch.setattr("lecture_pipeline.automation.watcher.parse_filename", lambda f: parse_filename(f, base_work_dir=work_base))
    monkeypatch.setattr("lecture_pipeline.automation.watcher.get_stt_channel", lambda cid: mock_channel)
    monkeypatch.setattr("lecture_pipeline.automation.watcher.notify_processing_started", MagicMock())

    with pytest.raises(RuntimeError, match="語音轉錄結果為空"):
        run_pipeline_for_group(
            audio_files=[audio_file],
            channel_id="C1",
            archive_dir=tmp_path / "archive",
            export_dir=tmp_path / "exports",
            upload_drive=False,
        )
    assert not (tmp_path / "exports" / "空音訊" / "20261119").exists()

