"""White-box test suite for QuotaManager, error classification, and DeadLetterQuarantine."""

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import pytest

from lecture_pipeline.automation.quota import (
    QuotaManager,
    is_api_or_quota_error,
    is_quota_exhausted_error,
    is_temporary_service_error,
)
from lecture_pipeline.automation.watcher import DeadLetterQuarantine


class TestQuotaManagerWhitebox:
    """White-box logic and edge case tests for QuotaManager."""

    def test_default_unlocked_state(self, tmp_path):
        qm = QuotaManager(state_file=tmp_path / "quota.json")
        locked, locked_until = qm.is_quota_locked()
        assert locked is False
        assert locked_until is None

    def test_mark_exhausted_and_lock_expiry(self, tmp_path):
        state_file = tmp_path / "quota.json"
        qm = QuotaManager(state_file=state_file, reset_hour_utc=8)

        now = datetime.now(timezone.utc)
        reset_time = qm.mark_quota_exhausted(reason="Test quota 429")

        assert reset_time > now
        assert state_file.is_file()

        locked, locked_until = qm.is_quota_locked()
        assert locked is True
        assert locked_until is not None

        # Simulate time moving past reset_time
        expired_state = {
            "locked_until": (now - timedelta(minutes=5)).isoformat(),
            "reason": "Old lock",
        }
        state_file.write_text(json.dumps(expired_state), encoding="utf-8")

        locked_expired, until_expired = qm.is_quota_locked()
        assert locked_expired is False

    def test_calculate_next_reset_datetime_boundaries(self):
        qm = QuotaManager(reset_hour_utc=8)

        # Before 8 AM UTC (e.g. 07:30 UTC) -> should be today 08:00 UTC
        dt_before = datetime(2026, 10, 29, 7, 30, 0, tzinfo=timezone.utc)
        reset_before = qm.calculate_next_reset_datetime(now=dt_before)
        assert reset_before == datetime(2026, 10, 29, 8, 0, 0, tzinfo=timezone.utc)

        # Exactly 8 AM UTC -> should be next day 08:00 UTC
        dt_exact = datetime(2026, 10, 29, 8, 0, 0, tzinfo=timezone.utc)
        reset_exact = qm.calculate_next_reset_datetime(now=dt_exact)
        assert reset_exact == datetime(2026, 10, 30, 8, 0, 0, tzinfo=timezone.utc)

        # After 8 AM UTC (e.g. 15:00 UTC) -> should be next day 08:00 UTC
        dt_after = datetime(2026, 10, 29, 15, 0, 0, tzinfo=timezone.utc)
        reset_after = qm.calculate_next_reset_datetime(now=dt_after)
        assert reset_after == datetime(2026, 10, 30, 8, 0, 0, tzinfo=timezone.utc)

    def test_corrupted_state_file_recovery(self, tmp_path):
        state_file = tmp_path / "corrupted_quota.json"
        state_file.write_text("NOT_JSON_DATA!!!", encoding="utf-8")

        qm = QuotaManager(state_file=state_file)
        locked, _ = qm.is_quota_locked()
        assert locked is False  # Safe fallback without raising


class TestErrorClassificationWhitebox:
    """White-box coverage for error categorization functions."""

    def test_temporary_503_detection(self):
        assert is_temporary_service_error(RuntimeError("503 Service Unavailable")) is True
        assert is_temporary_service_error(Exception("The model is overloaded. Please try again later.")) is True
        assert is_temporary_service_error(ValueError("Resource has been exhausted (429)")) is False
        assert is_temporary_service_error(TypeError("Unrelated error")) is False

    def test_quota_429_detection(self):
        assert is_quota_exhausted_error(Exception("429 ResourceExhausted: Quota exceeded")) is True
        assert is_quota_exhausted_error(RuntimeError("RESOURCE_EXHAUSTED")) is True
        assert is_quota_exhausted_error(RuntimeError("503 Service Unavailable")) is False

    def test_general_api_or_network_error_detection(self):
        assert is_api_or_quota_error(TimeoutError("Connection timed out")) is True
        assert is_api_or_quota_error(ConnectionResetError("Connection reset by peer")) is True
        assert is_api_or_quota_error(Exception("503 Server Error")) is True
        assert is_api_or_quota_error(Exception("429 Too Many Requests")) is True
        assert is_api_or_quota_error(KeyError("missing_key")) is False


class TestDeadLetterQuarantineWhitebox:
    """White-box testing for DeadLetterQuarantine edge cases."""

    def test_retry_increments_and_quarantine_after_max_retries(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        audio = inbox / "lecture.mp3"
        audio.write_bytes(b"dummy_bytes")

        dl = DeadLetterQuarantine(inbox, max_retries=3)

        # 1st failure
        assert dl.record_failure([audio], exc=RuntimeError("Corrupt audio header")) is False
        assert audio.is_file()

        # 2nd failure
        assert dl.record_failure([audio], exc=RuntimeError("Corrupt audio header")) is False
        assert audio.is_file()

        # 3rd failure -> Quarantined
        assert dl.record_failure([audio], exc=RuntimeError("Corrupt audio header")) is True
        assert not audio.exists()
        assert (inbox / "failed" / "lecture.mp3").is_file()

    def test_success_resets_failure_count(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        audio = inbox / "lecture.mp3"
        audio.write_bytes(b"dummy")

        dl = DeadLetterQuarantine(inbox, max_retries=3)
        dl.record_failure([audio], exc=RuntimeError("Transient error"))
        stats = dl._load_stats()
        assert stats["lecture.mp3"] == 1

        dl.record_success([audio])
        stats = dl._load_stats()
        assert "lecture.mp3" not in stats

    def test_quarantine_exemptions_never_moved(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        audio = inbox / "exempt.mp3"
        audio.write_bytes(b"content")

        dl = DeadLetterQuarantine(inbox, max_retries=3)

        # 1. API or Quota error
        for _ in range(5):
            dl.record_failure([audio], exc=Exception("429 Quota Exceeded"))
        assert audio.is_file()

        # 2. OS path syntax error (WinError 123)
        path_err = OSError("檔案名稱語法錯誤")
        setattr(path_err, "winerror", 123)
        for _ in range(5):
            dl.record_failure([audio], exc=path_err)
        assert audio.is_file()

        # 3. Aborted by user
        for _ in range(5):
            dl.record_failure([audio], exc=RuntimeError("任務已中止"))
        assert audio.is_file()
