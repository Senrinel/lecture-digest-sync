"""Black-box fault injection test suite.

Simulates and verifies system resilience against:
1. Damaged/corrupted audio files (3-retry dead letter quarantine).
2. Transient 503 / upstream server spikes (2-min backoff retries & stage checkpoint preservation).
3. 429 Daily quota exhaustion (next-day lock deferral, exemption from quarantine).
4. Low disk space (< 3.0 GB safety threshold).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from lecture_pipeline.automation.quota import QuotaManager, is_quota_exhausted_error, is_temporary_service_error
from lecture_pipeline.automation.watcher import DeadLetterQuarantine, check_disk_space, run_pipeline_for_group
from lecture_pipeline.cleanup.base import CleanupResult
from lecture_pipeline.naming import parse_filename
from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt.base import FullTranscription


class TestBlackBoxFaultInjection:
    """End-to-end fault injection scenarios for the lecture pipeline."""

    def test_corrupted_audio_quarantined_after_3_retries(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        corrupt_audio = inbox / "損壞課程音檔.mp3"
        corrupt_audio.write_bytes(b"NON_AUDIO_GARBAGE_BYTES_1234567890")

        quarantine = DeadLetterQuarantine(inbox, max_retries=3)

        decode_err = RuntimeError("FFmpeg Error: Invalid data found when processing input")

        # 1st attempt: Failure recorded, file remains in inbox
        quarantined = quarantine.record_failure([corrupt_audio], exc=decode_err)
        assert quarantined is False
        assert corrupt_audio.is_file()

        # 2nd attempt: Failure recorded, file remains in inbox
        quarantined = quarantine.record_failure([corrupt_audio], exc=decode_err)
        assert quarantined is False
        assert corrupt_audio.is_file()

        # 3rd attempt: Threshold reached -> File automatically moved to inbox/failed/
        with patch("lecture_pipeline.automation.watcher.notify_corrupted_file") as mock_notify:
            quarantined = quarantine.record_failure([corrupt_audio], exc=decode_err)
            assert quarantined is True
            assert not corrupt_audio.exists()
            failed_file = inbox / "failed" / "損壞課程音檔.mp3"
            assert failed_file.is_file()
            mock_notify.assert_called_once_with("損壞課程音檔.mp3")

    @patch("lecture_pipeline.automation.watcher.notify_processing_started")
    @patch("lecture_pipeline.automation.watcher.preprocess_audio")
    @patch("lecture_pipeline.automation.watcher.segment_audio")
    @patch("lecture_pipeline.automation.watcher.get_stt_channel")
    @patch("lecture_pipeline.automation.watcher.GeminiTextCleanupEngine")
    @patch("lecture_pipeline.automation.watcher.GeminiLectureSummarizer")
    @patch("time.sleep")
    def test_transient_503_retry_and_recovery(
        self,
        mock_sleep,
        mock_summarizer_cls,
        mock_cleaner_cls,
        mock_stt_channel,
        mock_segment,
        mock_preprocess,
        mock_notify_start,
        tmp_path,
    ):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        archive = tmp_path / "archive"
        archive.mkdir()
        work_base = tmp_path / "work"
        work_base.mkdir()

        audio_file = inbox / "統計學_20261020.mp3"
        audio_file.write_bytes(b"dummy_stream")

        def custom_parse(f):
            return parse_filename(f, base_work_dir=work_base)

        with patch("lecture_pipeline.automation.watcher.parse_filename", side_effect=custom_parse):
            mock_preprocess.side_effect = lambda src, dst, opts: dst.write_bytes(b"norm")
            mock_segment.return_value = [
                ChunkInfo(
                    index=1,
                    filename="chunk_001.wav",
                    filepath=str(work_base / "chunk_001.wav"),
                    start_sec=0.0,
                    end_sec=30.0,
                    duration_sec=30.0,
                )
            ]

            # STT succeeds
            channel_mock = MagicMock()
            channel_mock.transcribe_all.return_value = FullTranscription(
                channel_name="C1",
                course="統計學",
                date="20261020",
                chunks=[],
                full_text="常態分佈與假設檢定是統計推論的核心。",
                timestamped_text="[00:00] 常態分佈與假設檢定是統計推論的核心。",
                total_latency_sec=1.0,
            )
            mock_stt_channel.return_value = channel_mock

            # Cleaner fails twice with 503, succeeds on 3rd attempt
            cleaner_mock = MagicMock()
            cleaner_mock.clean.side_effect = [
                RuntimeError("503 Service Unavailable: High demand"),
                RuntimeError("The model is overloaded. Please try again later (503)"),
                CleanupResult(
                    full_cleaned_text="常態分佈與假設檢定是統計推論的核心。",
                    raw_char_count=21,
                    cleaned_char_count=21,
                    retention_ratio=1.0,
                    model_used="gemini-3.5-flash-lite",
                    prompt_template="videolingo_punct.md",
                    total_latency_sec=0.5,
                ),
            ]
            mock_cleaner_cls.return_value = cleaner_mock

            # Summarizer succeeds
            summarizer_mock = MagicMock()
            from lecture_pipeline.summary.base import SummaryResult
            summarizer_mock.summarize.return_value = SummaryResult(
                full_summary_md="# 統計學筆記",
                input_char_count=21,
                summary_char_count=10,
                model_used="gemini-3.5-flash-lite",
                prompt_template="fabric_summarize_lecture.md",
                total_latency_sec=0.5,
            )
            mock_summarizer_cls.return_value = summarizer_mock

            # Run pipeline
            res_dir = run_pipeline_for_group(
                audio_files=[audio_file],
                channel_id="C1",
                archive_dir=archive,
                export_dir=tmp_path / "exports",
                record_archive_base_dir=tmp_path / "Record",
                upload_drive=False,
            )

            # Verification:
            # 1. Cleaner retried 3 times and ultimately succeeded
            assert cleaner_mock.clean.call_count == 3
            # 2. Output files were generated
            assert (res_dir / "02_cleaned.txt").is_file()
            # 3. Audio file successfully archived
            assert not audio_file.exists()

    def test_api_429_quota_exhaustion_never_quarantines_audio(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        audio = inbox / "國際關係_20261020.mp3"
        audio.write_bytes(b"content")

        quarantine = DeadLetterQuarantine(inbox, max_retries=3)
        quota_err = Exception("429 ResourceExhausted: You have exceeded your current quota.")

        # Simulate 10 repeated quota exhaustion errors
        for _ in range(10):
            quarantined = quarantine.record_failure([audio], exc=quota_err)
            assert quarantined is False

        # Audio file MUST remain untouched in inbox
        assert audio.is_file()
        assert not (inbox / "failed" / "國際關係_20261020.mp3").exists()

    def test_quota_manager_locks_and_defers_system(self, tmp_path):
        lock_file = tmp_path / "quota_lock.json"
        qm = QuotaManager(lock_file=lock_file, daily_reset_hour_utc=8)

        # Before lock
        is_locked, _ = qm.is_quota_locked()
        assert is_locked is False

        # Mark 429 quota exhausted
        reset_time = qm.mark_quota_exhausted(reason="429 ResourceExhausted")
        assert lock_file.is_file()

        is_locked, locked_until = qm.is_quota_locked()
        assert is_locked is True
        assert locked_until is not None

        # Unlock
        qm.unlock_quota()
        is_locked_after, _ = qm.is_quota_locked()
        assert is_locked_after is False
        assert not lock_file.exists()

    def test_low_disk_space_guard_aborts_early(self, tmp_path):
        # When volume has only 1.2 GB free (< 3.0 GB requirement)
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = type("Usage", (), {"free": int(1.2 * (1024**3))})()
            has_space, free_gb = check_disk_space(tmp_path, min_free_gb=3.0)
            assert has_space is False
            assert free_gb == 1.2
