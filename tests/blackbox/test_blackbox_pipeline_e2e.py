"""Black-box end-to-end pipeline test suite.

Verifies inbox scanning & grouping, complete pipeline execution from audio input
to notebook bundle & archive, and stage-level checkpoint resumption.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from lecture_pipeline.automation.watcher import run_pipeline_for_group, scan_and_group_inbox
from lecture_pipeline.cleanup.base import CleanupResult
from lecture_pipeline.naming import parse_filename
from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt.base import FullTranscription
from lecture_pipeline.summary.base import SummaryResult


class TestBlackBoxPipelineE2E:
    """Black-box tests for file grouping, end-to-end processing, and checkpoint resumption."""

    def test_inbox_scanning_and_grouping(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # 1. Standard single-part lecture
        f1 = inbox / "經濟學原理_20261020.mp3"
        f1.write_bytes(b"dummy_audio_1")

        # 2. Multi-part university lecture with (1), (2) naming
        f2 = inbox / "商事法 02 (1).mp3"
        f3 = inbox / "商事法 02 (2).mp3"
        f2.write_bytes(b"dummy_audio_2")
        f3.write_bytes(b"dummy_audio_3")

        # 3. Another single-part standard lecture
        f4 = inbox / "民法總則_20261022.mp3"
        f4.write_bytes(b"dummy_audio_4")

        # 4. SONY IC recorder format (YYMMDD_HHMM)
        f5 = inbox / "261023_0930.MP3"
        f5.write_bytes(b"dummy_audio_5")

        # 5. Ignored non-audio or system files
        (inbox / "thumbs.db").write_bytes(b"system")
        (inbox / ".DS_Store").write_bytes(b"system")
        (inbox / "lecture_slides.pdf").write_bytes(b"pdf_content")

        groups = scan_and_group_inbox(inbox)

        # Check total number of distinct lectures detected
        assert len(groups) == 4

        # Check multipart grouping
        shang_key = [k for k in groups if "商事法" in k][0]
        assert len(groups[shang_key]) == 2
        assert groups[shang_key][0].name == "商事法 02 (1).mp3"
        assert groups[shang_key][1].name == "商事法 02 (2).mp3"

    @patch("lecture_pipeline.automation.watcher.notify_processing_started")
    @patch("lecture_pipeline.automation.watcher.preprocess_audio")
    @patch("lecture_pipeline.automation.watcher.segment_audio")
    @patch("lecture_pipeline.automation.watcher.get_stt_channel")
    @patch("lecture_pipeline.automation.watcher.GeminiTextCleanupEngine")
    @patch("lecture_pipeline.automation.watcher.GeminiLectureSummarizer")
    @patch("lecture_pipeline.automation.watcher.prune_and_archive")
    def test_end_to_end_pipeline_success(
        self,
        mock_prune,
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
        exports = tmp_path / "exports"
        exports.mkdir()
        work_base = tmp_path / "work"
        work_base.mkdir()

        audio_file = inbox / "演算法概論_20261020.mp3"
        audio_file.write_bytes(b"fake_mp3_stream")

        def custom_parse(f):
            return parse_filename(f, base_work_dir=work_base)

        with patch("lecture_pipeline.automation.watcher.parse_filename", side_effect=custom_parse):
            # Setup mocks for processing steps
            mock_preprocess.side_effect = lambda src, dst, opts: dst.write_bytes(b"norm_wav")
            mock_segment.return_value = [
                ChunkInfo(
                    index=1,
                    filename="chunk_001.wav",
                    filepath=str(work_base / "chunk_001.wav"),
                    start_sec=0.0,
                    end_sec=60.0,
                    duration_sec=60.0,
                )
            ]

            # Mock STT
            channel_mock = MagicMock()
            channel_mock.transcribe_all.return_value = FullTranscription(
                channel_name="C1",
                course="演算法概論",
                date="20261020",
                chunks=[],
                full_text="今天我們來介紹動態規劃演算法的核心概念。",
                timestamped_text="[00:00] 今天我們來介紹動態規劃演算法的核心概念。",
                total_latency_sec=1.2,
            )
            mock_stt_channel.return_value = channel_mock

            # Mock Cleaner
            cleaner_mock = MagicMock()
            cleaner_mock.clean.return_value = CleanupResult(
                full_cleaned_text="今天我們來介紹動態規劃演算法的核心概念。",
                raw_char_count=21,
                cleaned_char_count=21,
                retention_ratio=1.0,
                model_used="gemini-3.5-flash-lite",
                prompt_template="videolingo_punct.md",
                total_latency_sec=0.5,
            )
            mock_cleaner_cls.return_value = cleaner_mock

            # Mock Summarizer
            summarizer_mock = MagicMock()
            summarizer_mock.summarize.return_value = SummaryResult(
                full_summary_md="# 演算法概論 重點筆記\n\n- 動態規劃 (Dynamic Programming) 核心在於狀態轉移方程式。",
                input_char_count=21,
                summary_char_count=45,
                model_used="gemini-3.5-flash-lite",
                prompt_template="fabric_summarize_lecture.md",
                total_latency_sec=0.5,
            )
            mock_summarizer_cls.return_value = summarizer_mock

            # Execute pipeline
            result_work_dir = run_pipeline_for_group(
                audio_files=[audio_file],
                channel_id="C1",
                archive_dir=archive,
                upload_drive=False,
                export_dir=exports,
                record_archive_base_dir=tmp_path / "Record",
            )

            # Assertions:
            # 1. Output files generated in work_dir
            assert (result_work_dir / "01_raw.txt").is_file()
            assert (result_work_dir / "02_cleaned.txt").is_file()
            assert (result_work_dir / "03_summary.md").is_file()
            assert (result_work_dir / "run.json").is_file()

            # 2. Original audio moved from inbox to archive
            assert not audio_file.exists()
            archived_file = archive / "演算法概論" / "20261020" / "演算法概論_20261020.mp3"
            assert archived_file.is_file()

            # 3. Notebook bundle exported under true course & date (not 'Lecture/<today>')
            bundle_dir = result_work_dir / "notebook_bundle"
            assert bundle_dir.is_dir()
            exported_mds = list(bundle_dir.glob("*.md"))
            assert len(exported_mds) >= 2
            assert (exports / "演算法概論" / "20261020").is_dir()

    @patch("lecture_pipeline.automation.watcher.notify_processing_started")
    @patch("lecture_pipeline.automation.watcher.get_stt_channel")
    @patch("lecture_pipeline.automation.watcher.GeminiTextCleanupEngine")
    @patch("lecture_pipeline.automation.watcher.GeminiLectureSummarizer")
    @patch("lecture_pipeline.automation.watcher.prune_and_archive")
    def test_pipeline_checkpoint_skips_completed_stages(
        self,
        mock_prune,
        mock_summarizer_cls,
        mock_cleaner_cls,
        mock_stt_channel,
        mock_notify_start,
        tmp_path,
    ):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        archive = tmp_path / "archive"
        archive.mkdir()
        exports = tmp_path / "exports"
        exports.mkdir()
        work_base = tmp_path / "work"
        work_base.mkdir()

        audio_file = inbox / "作業系統_20261020.mp3"
        audio_file.write_bytes(b"audio_bytes")

        def custom_parse(f):
            return parse_filename(f, base_work_dir=work_base)

        parsed_work_dir = work_base / "作業系統" / "20261020"
        parsed_work_dir.mkdir(parents=True, exist_ok=True)

        # Pre-seed stage checkpoints: 00_normalized.wav, chunks/, 01_raw.txt, 02_cleaned.txt
        norm_wav = parsed_work_dir / "00_normalized.wav"
        norm_wav.write_bytes(b"normalized_data")

        chunks_dir = parsed_work_dir / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "chunk_001.wav").write_bytes(b"chunk_data")

        raw_txt = parsed_work_dir / "01_raw.txt"
        raw_txt.write_text("既有逐字稿內容，無需重新轉錄。", encoding="utf-8")

        cleaned_txt = parsed_work_dir / "02_cleaned.txt"
        cleaned_txt.write_text("既有整理文稿內容，無需重新整理。", encoding="utf-8")

        # Mock Summarizer
        summarizer_mock = MagicMock()
        summarizer_mock.summarize.return_value = SummaryResult(
            full_summary_md="# 作業系統 重點筆記",
            input_char_count=18,
            summary_char_count=12,
            model_used="gemini-3.5-flash-lite",
            prompt_template="fabric_summarize_lecture.md",
            total_latency_sec=0.5,
        )
        mock_summarizer_cls.return_value = summarizer_mock

        with patch("lecture_pipeline.automation.watcher.parse_filename", side_effect=custom_parse):
            run_pipeline_for_group(
                audio_files=[audio_file],
                channel_id="C1",
                archive_dir=archive,
                upload_drive=False,
                export_dir=exports,
                record_archive_base_dir=tmp_path / "Record",
            )

            # Verification:
            # 1. STT channel was NEVER called
            mock_stt_channel.assert_not_called()

            # 2. Cleaner was NEVER called
            mock_cleaner_cls.assert_not_called()

            # 3. Summarizer WAS called to complete the unfinished stage
            summarizer_mock.summarize.assert_called_once()

            # 4. Summary output generated and audio archived
            assert (parsed_work_dir / "03_summary.md").is_file()
            assert not audio_file.exists()
