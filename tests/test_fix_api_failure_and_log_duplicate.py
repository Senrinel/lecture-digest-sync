"""Unit tests verifying fixes for:
1. API call failures must NOT display "檔案損壞放棄處理" or move files to dead letter quarantine,
   and must preserve progress and defer to the next day.
2. Step logs on the recording processing page (task-live-logs) must NOT display duplicates.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lecture_pipeline.automation.notifier import notify_corrupted_file
from lecture_pipeline.automation.quota import QuotaManager
from lecture_pipeline.automation.watcher import (
    DeadLetterQuarantine,
    acquire_processing_files,
    is_api_or_quota_error,
    is_quota_or_rate_limit_error,
    release_processing_files,
)
from lecture_pipeline.gui.bridge import (
    _progress_debounce_lock,
    broadcaster,
    get_current_task_status,
    update_task_progress,
)


def test_api_and_quota_error_detection():
    """Verify various API and network errors are identified as API/quota issues,

    and non-API errors (e.g. FileNotFoundError) are not.
    """
    # 1. Standard quota / 429
    e_429 = RuntimeError("429 RESOURCE_EXHAUSTED: Rate limit exceeded")
    assert is_api_or_quota_error(e_429) is True
    assert is_quota_or_rate_limit_error(e_429) is True

    # 2. 503 / 500 / Service Unavailable / Gateway Timeout
    e_503 = Exception("503 Service Unavailable: The model is overloaded. Please try again later.")
    assert is_api_or_quota_error(e_503) is True
    assert is_quota_or_rate_limit_error(e_503) is True

    e_500 = Exception("500 Internal Server Error: An unexpected error occurred on the server.")
    assert is_api_or_quota_error(e_500) is True

    # 3. Connection and Timeout
    e_conn = ConnectionResetError("Connection reset by peer while reading API response")
    assert is_api_or_quota_error(e_conn) is True

    e_timeout = TimeoutError("Deadline exceeded waiting for Gemini API response")
    assert is_api_or_quota_error(e_timeout) is True

    # 4. Standard file or format errors must NOT be marked as API error
    e_fnf = FileNotFoundError("Cannot find input audio file")
    assert is_api_or_quota_error(e_fnf) is False


def test_dead_letter_quarantine_skips_api_errors(tmp_path: Path):
    """DeadLetterQuarantine must NEVER quarantine files or send corrupted notification on API failures."""
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    audio_file = inbox_dir / "演算法_20261019.mp3"
    audio_file.write_bytes(b"dummy audio data")

    quarantine = DeadLetterQuarantine(inbox_dir, max_retries=3)

    api_err = RuntimeError("503 Service Unavailable: model overloaded")

    # Calling record_failure with API error 3 times
    for _ in range(5):
        quarantined = quarantine.record_failure([audio_file], exc=api_err)
        assert quarantined is False

    # File must STILL be in inbox, never moved to failed/
    assert audio_file.is_file()
    assert not (inbox_dir / "failed" / audio_file.name).exists()


def test_notify_corrupted_file_never_triggered_on_api_error(tmp_path: Path):
    """Verify that notify_corrupted_file is NEVER triggered when handling an API error."""
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    audio_file = inbox_dir / "test.mp3"
    audio_file.write_bytes(b"data")

    quarantine = DeadLetterQuarantine(inbox_dir, max_retries=3)
    mock_notify = MagicMock()

    with patch("lecture_pipeline.automation.watcher.notify_corrupted_file", mock_notify):
        api_err = RuntimeError("429 Quota Exceeded / 503 Overloaded")
        for _ in range(5):
            res = quarantine.record_failure([audio_file], exc=api_err)
            assert res is False

    mock_notify.assert_not_called()
    assert audio_file.is_file()


def test_progress_broadcast_deduplication():
    """Consecutive duplicate progress updates within 1s must be suppressed at the backend."""
    broadcasted = []

    def mock_broadcast(msg):
        broadcasted.append(msg)

    with patch.object(broadcaster, "broadcast_sync", side_effect=mock_broadcast):
        # Reset debounce state
        import lecture_pipeline.gui.bridge as b
        b._last_progress_sig = None
        b._last_progress_time = 0.0

        # First call: should broadcast
        update_task_progress(
            stage="音訊前處理",
            progress=15,
            detail="執行 16kHz 單聲道轉換...",
            task_name="演算法",
            running=True,
        )
        assert len(broadcasted) == 1

        # Second call immediately after with identical parameters: should be debounced
        update_task_progress(
            stage="音訊前處理",
            progress=15,
            detail="執行 16kHz 單聲道轉換...",
            task_name="演算法",
            running=True,
        )
        assert len(broadcasted) == 1

        # Third call with new stage/progress: should broadcast
        update_task_progress(
            stage="VAD 切段",
            progress=30,
            detail="執行 Silero VAD 靜音切段...",
            task_name="演算法",
            running=True,
        )
        assert len(broadcasted) == 2

        # Reset running state to False
        update_task_progress(
            stage="完成",
            progress=100,
            detail="處理完成",
            task_name="演算法",
            running=False,
        )


def test_task_locking_prevents_overlap(tmp_path: Path):
    """Acquiring active processing files prevents concurrent processing on the same file."""
    f1 = tmp_path / "test1.wav"
    f2 = tmp_path / "test2.wav"
    for f in [f1, f2]:
        f.touch()

    # First acquisition succeeds
    assert acquire_processing_files([f1, f2]) is True

    # Concurrent attempt on f1 must fail
    assert acquire_processing_files([f1]) is False

    # Release f1, f2
    release_processing_files([f1, f2])

    # Acquisition now succeeds again
    assert acquire_processing_files([f1]) is True
    release_processing_files([f1])


def test_task_locking_reentrant_with_same_token(tmp_path: Path):
    """Acquiring with the same lock token succeeds (re-entrant), while different token fails."""
    f = tmp_path / "lecture.wav"
    f.touch()
    token = "task_123"
    assert acquire_processing_files([f], token=token) is True
    # Re-entrant with same token succeeds
    assert acquire_processing_files([f], token=token) is True
    # Different token fails
    assert acquire_processing_files([f], token="task_456") is False
    # Release with token
    release_processing_files([f], token=token)
    # Now different token succeeds
    assert acquire_processing_files([f], token="task_456") is True
    release_processing_files([f], token="task_456")
