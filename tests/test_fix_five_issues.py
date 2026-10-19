"""Tests verifying fixes for the 5 user-reported issues:
1. Audio player linking precision (no cross-lecture audio contamination).
2. Window top-right X button minimizes/hides to system tray while background watcher continues.
3. Audio processing notification debouncing and task locking (no duplicate toast).
4. VS Code Markdown Preview integration for note opening.
5. Stage-wise checkpoints (斷點續傳) and API quota limit next-day deferral without quarantine abandonment.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from lecture_pipeline.automation.notifier import (
    _last_notifications,
    send_notification,
)
from lecture_pipeline.automation.quota import QuotaManager
from lecture_pipeline.automation.watcher import (
    acquire_processing_files,
    is_quota_or_rate_limit_error,
    release_processing_files,
    run_pipeline_for_group,
)
from lecture_pipeline.gui.bridge import (
    open_file_in_vscode_preview,
    resolve_lecture_audio_path,
)
from lecture_pipeline.gui.server import app
from lecture_pipeline.gui.window import DesktopWindow
from lecture_pipeline.naming import parse_filename
from lecture_pipeline.state import PreprocessOptions, RunState, SegmentationOptions
from lecture_pipeline.stt.base import ChunkTranscription, FullTranscription


def test_issue1_audio_linking_precision(tmp_path: Path, monkeypatch):
    """Issue 1: Player audio must precisely bind to the specific course & date,

    never accidentally grabbing another course's audio or a failed task's 00_normalized.wav.
    """
    export_dir = tmp_path / "exports"
    rec_archive = tmp_path / "Record"
    work_dir = tmp_path / "work"
    archive_dir = tmp_path / "archive"

    for d in [export_dir, rec_archive, work_dir, archive_dir]:
        d.mkdir(parents=True, exist_ok=True)

    date_str = "20261019"
    # Course A: Completed lecture
    course_a = "演算法"
    exp_a = export_dir / course_a / date_str
    exp_a.mkdir(parents=True, exist_ok=True)
    (exp_a / "03_summary.md").write_text("# 演算法重點", encoding="utf-8")

    rec_a = rec_archive / course_a / date_str
    rec_a.mkdir(parents=True, exist_ok=True)
    audio_a = rec_a / f"{course_a}_{date_str}_audio.opus"
    audio_a.write_text("audio data A", encoding="utf-8")

    # Course B: Failed lecture sitting in work/ with 00_normalized.wav from the same date!
    course_b = "數位邏輯"
    work_b = work_dir / course_b / date_str
    work_b.mkdir(parents=True, exist_ok=True)
    bad_audio_b = work_b / "00_normalized.wav"
    bad_audio_b.write_text("bad audio B", encoding="utf-8")

    # Mock config to point to our test directories
    monkeypatch.setattr(
        "lecture_pipeline.gui.bridge.get_current_config",
        lambda: {
            "paths": {
                "record_archive_dir": str(rec_archive),
                "archive_dir": str(archive_dir),
                "work_dir": str(work_dir),
            },
            "export": {"export_dir": str(export_dir)},
        },
    )

    # Resolving Course A MUST find audio_a, and MUST NEVER grab bad_audio_b!
    found_a = resolve_lecture_audio_path(exp_a, course_name=course_a, date_str=date_str)
    assert found_a is not None
    assert found_a.resolve() == audio_a.resolve()
    assert "數位邏輯" not in str(found_a)
    assert "00_normalized" not in str(found_a)

    # Resolving Course B without record archive must find work_b specifically, and never Course A
    found_b = resolve_lecture_audio_path(work_b, course_name=course_b, date_str=date_str)
    assert found_b is not None
    assert found_b.resolve() == bad_audio_b.resolve()
    assert "演算法" not in str(found_b)


def test_issue2_window_close_minimizes_to_tray():
    """Issue 2: Clicking top-right X should hide the window, not destroy it.

    destroy() should force close when user explicitly chooses '結束程式' from tray.
    """
    win = DesktopWindow(title="Test Window")

    class EventSlot:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    class FakeEvents:
        def __init__(self):
            self.closing = EventSlot()

    fake_window = MagicMock()
    fake_window.events = FakeEvents()

    with patch("webview.create_window", return_value=fake_window):
        win.create(hidden=False)

    assert len(fake_window.events.closing.handlers) == 1
    on_closing = fake_window.events.closing.handlers[0]

    # Test 1: User clicks X (normal close attempt)
    win.hide = MagicMock()
    result = on_closing()
    # Must return False to cancel window destruction, and must call hide()
    assert result is False
    win.hide.assert_called_once()

    # Test 2: User explicitly terminates application via Tray '結束程式'
    win.destroy()
    assert win._force_close is True
    result_exit = on_closing()
    # Must return True to permit closing
    assert result_exit is True


def test_issue3_notification_debouncing_and_task_lock(tmp_path: Path):
    """Issue 3: Notifications must not repeat twice, and concurrent tasks on same file must lock."""
    # 1. Debounce test
    _last_notifications.clear()
    sent_items = []

    mock_win11 = MagicMock()
    mock_win11.toast = lambda title, message, **kwargs: sent_items.append((title, message))

    with patch.dict("sys.modules", {"win11toast": mock_win11}):
        # First notification
        send_notification("Test Title", "Test Message", sync=True, debounce_sec=5.0)
        # Duplicate notification immediately after
        send_notification("Test Title", "Test Message", sync=True, debounce_sec=5.0)

    # Only 1 toast should have been sent within the debounce window
    assert len(sent_items) == 1
    assert sent_items[0] == ("Test Title", "Test Message")

    # 2. File lock test
    file1 = tmp_path / "lecture_part1.mp3"
    file2 = tmp_path / "lecture_part2.mp3"
    file1.touch()
    file2.touch()

    # Worker 1 acquires files
    assert acquire_processing_files([file1, file2]) is True
    # Worker 2 attempts same files -> must be rejected
    assert acquire_processing_files([file1]) is False
    assert acquire_processing_files([file2]) is False

    # Worker 1 releases files
    release_processing_files([file1, file2])
    # Now worker 2 can acquire
    assert acquire_processing_files([file1]) is True
    release_processing_files([file1])


def test_issue4_vscode_preview_open(tmp_path: Path, monkeypatch):
    """Issue 4: Opening summary note should invoke VS Code preview mode."""
    note_file = tmp_path / "01_TestCourse_20261019_重點總結筆記.md"
    note_file.write_text("# 課堂重點筆記\n\n內容", encoding="utf-8")

    # In test mode
    monkeypatch.setenv("LECTURE_PIPELINE_TEST_MODE", "1")
    assert open_file_in_vscode_preview(note_file) is True

    # Test via FastAPI endpoint
    client = TestClient(app)
    resp = client.post("/api/system/open-file", json={
        "lecture_id": "test_id",
        "file_type": "summary",
        "custom_path": str(note_file),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "重點總結筆記" in data["path"]


def test_issue5_pipeline_stage_checkpoints(tmp_path: Path, monkeypatch):
    """Issue 5: Resuming execution should skip stages with existing outputs (00_normalized, chunks, 01_raw.json)."""
    audio_file = tmp_path / "測試課程_20261019.mp3"
    audio_file.write_bytes(b"dummy mp3 data")

    parsed = parse_filename(audio_file)
    work_dir = parsed.work_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Seed intermediate outputs in parsed.work_dir
        norm_wav = work_dir / "00_normalized.wav"
        norm_wav.write_bytes(b"RIFF dummy wav data")

        chunks_dir = work_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        chunk1 = chunks_dir / "chunk_0000.wav"
        chunk1.write_bytes(b"chunk 0 data")

        raw_json = work_dir / "01_raw.json"
        transcription = FullTranscription(
            channel_name="C1",
            course="測試課程",
            date="20261019",
            chunks=[
                ChunkTranscription(
                    index=0,
                    start_sec=0.0,
                    end_sec=5.0,
                    duration_sec=5.0,
                    raw_text="既有逐字稿測試文字",
                    model_used="test",
                    latency_sec=0.1,
                )
            ],
            full_text="既有逐字稿測試文字，不應重複呼叫 STT API。",
            timestamped_text="[00:00:00] 既有逐字稿測試文字",
            total_latency_sec=1.0,
        )
        raw_json.write_text(transcription.model_dump_json(), encoding="utf-8")
        (work_dir / "01_raw.txt").write_text(transcription.full_text, encoding="utf-8")

        # Mocks to verify which functions are called
        mock_preprocess = MagicMock()
        mock_segment = MagicMock()
        mock_transcribe = MagicMock()

        monkeypatch.setattr("lecture_pipeline.automation.watcher.preprocess_audio", mock_preprocess)
        monkeypatch.setattr("lecture_pipeline.automation.watcher.segment_audio", mock_segment)

        mock_channel = MagicMock()
        mock_channel.transcribe_all = mock_transcribe
        monkeypatch.setattr("lecture_pipeline.automation.watcher.get_stt_channel", lambda cid: mock_channel)

        # Mock cleanup and summary so pipeline finishes
        mock_cleaner = MagicMock()
        mock_clean_res = MagicMock()
        mock_clean_res.full_cleaned_text = "整理後文字"
        mock_clean_res.model_dump_json.return_value = "{}"
        mock_cleaner.clean.return_value = mock_clean_res
        monkeypatch.setattr("lecture_pipeline.automation.watcher.GeminiTextCleanupEngine", lambda **kw: mock_cleaner)

        mock_summarizer = MagicMock()
        mock_sum_res = MagicMock()
        mock_sum_res.full_summary_md = "# 總結"
        mock_sum_res.model_dump_json.return_value = "{}"
        mock_summarizer.summarize.return_value = mock_sum_res
        monkeypatch.setattr("lecture_pipeline.automation.watcher.GeminiLectureSummarizer", lambda **kw: mock_summarizer)

        monkeypatch.setattr("lecture_pipeline.automation.watcher.prepare_notebook_bundle", MagicMock())
        monkeypatch.setattr("lecture_pipeline.automation.watcher.prune_and_archive", MagicMock())

        out = run_pipeline_for_group(
            audio_files=[audio_file],
            channel_id="C1",
            archive_dir=tmp_path / "archive",
            upload_drive=False,
        )

        # Checkpoint verification: Preprocess, VAD segment, and STT transcribe must NOT be called!
        assert mock_preprocess.call_count == 0
        assert mock_segment.call_count == 0
        assert mock_transcribe.call_count == 0

        # Cleanup and summarize should be executed from the resumed transcript
        assert mock_cleaner.clean.call_count == 1
        assert mock_summarizer.summarize.call_count == 1
    finally:
        # Clean up seeded test work_dir
        if work_dir.is_dir():
            shutil.rmtree(work_dir, ignore_errors=True)


def test_issue5_quota_error_detection_and_deferral(tmp_path: Path):
    """Issue 5: API 429 / ResourceExhausted must trigger quota deferral without moving to failed quarantine."""
    qm = QuotaManager(lock_file=tmp_path / "quota_lock.json")

    # Test error detector
    e_quota1 = RuntimeError("429 RESOURCE_EXHAUSTED: You have exceeded your quota")
    e_quota2 = Exception("Rate limit exceeded. Please wait before retrying.")
    e_normal = FileNotFoundError("Missing file")

    assert is_quota_or_rate_limit_error(e_quota1) is True
    assert is_quota_or_rate_limit_error(e_quota2) is True
    assert is_quota_or_rate_limit_error(e_normal) is False

    # Mark quota exhausted
    reset_dt = qm.mark_quota_exhausted(reason="429 RESOURCE_EXHAUSTED")
    is_locked, until = qm.is_quota_locked()
    assert is_locked is True
    assert until is not None

    # Defer task
    deferred_file = qm.defer_task("cleanup", tmp_path, "測試課程", "20261019")
    assert deferred_file.is_file()
    data = json.loads(deferred_file.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["course"] == "測試課程"
    assert data[0]["task_type"] == "cleanup"
