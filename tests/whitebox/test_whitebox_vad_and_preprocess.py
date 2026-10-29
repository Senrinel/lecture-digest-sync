"""White-box tests for PreprocessOptions, VAD cut point algorithms, and file/disk stability checks."""

from pathlib import Path
import os
import pytest
from unittest.mock import patch

from lecture_pipeline.state import PreprocessOptions
from lecture_pipeline.vad_segment import calculate_cut_points
from lecture_pipeline.automation.watcher import check_disk_space, is_file_stable


class TestPreprocessOptionsWhitebox:
    """White-box verification for PreprocessOptions CLI parsing and default fallbacks."""

    def test_default_empty_or_none(self):
        opts_none = PreprocessOptions.from_cli_flags(None)
        assert opts_none.hp == "off"
        assert opts_none.loud == "off"
        assert opts_none.dyn == "off"
        assert opts_none.nr == "off"
        assert opts_none.dfn == "off"
        assert opts_none.se == "off"
        assert opts_none.wpe == "off"
        assert opts_none.vadtrim == "off"

        opts_empty = PreprocessOptions.from_cli_flags("")
        assert opts_empty.hp == "off"
        assert opts_empty.loud == "off"

    def test_bare_active_defaults(self):
        opts = PreprocessOptions.from_cli_flags("hp,loud,dyn,nr,dfn,se,wpe,vadtrim")
        assert opts.hp == "100Hz"
        assert opts.loud == "-19LUFS"
        assert opts.dyn == "standard"
        assert opts.nr == "12dB"
        assert opts.dfn == "on"
        assert opts.se == "on"
        assert opts.wpe == "on"
        assert opts.vadtrim == "on"

    def test_custom_key_value_assignments(self):
        opts = PreprocessOptions.from_cli_flags("hp=120Hz, loud=-16LUFS, dyn=light, nr=6dB")
        assert opts.hp == "120Hz"
        assert opts.loud == "-16LUFS"
        assert opts.dyn == "light"
        assert opts.nr == "6dB"
        assert opts.dfn == "off"  # untouched

    def test_whitespace_and_case_insensitivity(self):
        opts = PreprocessOptions.from_cli_flags("  HP=80Hz ,   LOUD=-23LUFS  , SE ")
        assert opts.hp == "80Hz"
        assert opts.loud == "-23LUFS"
        assert opts.se == "on"

    def test_unknown_tokens_ignored_safely(self):
        opts = PreprocessOptions.from_cli_flags("unknown_flag, foo=bar, hp=100Hz")
        assert opts.hp == "100Hz"
        assert not hasattr(opts, "unknown_flag")
        assert not hasattr(opts, "foo")


class TestVadCutPointCalculationWhitebox:
    """White-box edge cases and math tests for calculate_cut_points."""

    def test_short_audio_no_cut_needed(self):
        # target_chunk_sec = 600, min_tail_sec = 30. Total <= 630 -> no cut
        points = calculate_cut_points(total_duration=500.0, silence_intervals=[], target_chunk_sec=600)
        assert points == [0.0, 500.0]

        points_exact = calculate_cut_points(total_duration=630.0, silence_intervals=[], target_chunk_sec=600)
        assert points_exact == [0.0, 630.0]

    def test_single_cut_at_silence_midpoint(self):
        # 1200 seconds audio. Search window around 600s is [570, 630].
        # Silence interval from 598.0 to 602.0. Midpoint = 600.0.
        silences = [(598.0, 602.0)]
        points = calculate_cut_points(
            total_duration=1200.0,
            silence_intervals=silences,
            target_chunk_sec=600,
            search_window_sec=30,
        )
        assert points[0] == 0.0
        assert points[1] == 600.0
        assert points[2] == 1200.0

    def test_prioritizes_longer_silence_interval(self):
        # Two silences in window:
        # candidate 1: 590 to 591 (1.0s long, near 600)
        # candidate 2: 605 to 608 (3.0s long, slightly further)
        # Should pick candidate 2 because of longer duration
        silences = [(590.0, 591.0), (605.0, 608.0)]
        points = calculate_cut_points(
            total_duration=1200.0,
            silence_intervals=silences,
            target_chunk_sec=600,
            search_window_sec=30,
        )
        assert points[1] == 606.5  # Midpoint of (605, 608)

    def test_fallback_to_broader_window(self):
        # Immediate window [570, 630] has NO silence.
        # Broader window [540, 660] has silence at 645~646.
        silences = [(645.0, 646.0)]
        points = calculate_cut_points(
            total_duration=1200.0,
            silence_intervals=silences,
            target_chunk_sec=600,
            search_window_sec=30,
        )
        assert points[1] == 645.5

    def test_no_silence_fallback_to_exact_target(self):
        # Pure tone / speech with zero silence intervals anywhere
        points = calculate_cut_points(
            total_duration=1500.0,
            silence_intervals=[],
            target_chunk_sec=600,
            search_window_sec=30,
        )
        assert points == [0.0, 600.0, 1200.0, 1500.0]

    def test_tail_suppression(self):
        # Total duration = 650. Target = 600. Tail = 50.
        # min_tail_sec = min(30, 180) = 30.
        # 600 + 30 = 630 <= 650 -> cuts at 600, remaining tail = 50.
        points = calculate_cut_points(
            total_duration=650.0,
            silence_intervals=[],
            target_chunk_sec=600,
            search_window_sec=30,
        )
        assert points == [0.0, 600.0, 650.0]

        # Total duration = 620. Tail is 20 < 30 -> should not cut!
        points_no_tail = calculate_cut_points(
            total_duration=620.0,
            silence_intervals=[],
            target_chunk_sec=600,
            search_window_sec=30,
        )
        assert points_no_tail == [0.0, 620.0]


class TestFileAndDiskStabilityWhitebox:
    """White-box verification for is_file_stable and check_disk_space edge cases."""

    def test_is_file_stable_nonexistent(self, tmp_path):
        assert is_file_stable(tmp_path / "ghost.wav", delay_sec=0.01) is False

    def test_is_file_stable_zero_bytes(self, tmp_path):
        zero_file = tmp_path / "empty.wav"
        zero_file.write_bytes(b"")
        assert is_file_stable(zero_file, delay_sec=0.01) is False

    def test_is_file_stable_growing_file(self, tmp_path):
        growing = tmp_path / "growing.wav"
        growing.write_bytes(b"initial_data")

        # Mock stat().st_size to simulate file growing between the two checks
        # Call 1: is_file()
        # Call 2: size1
        # Call 3: size2 (grown)
        real_stat = growing.stat()
        sizes = [100, 100, 200]

        class MockStat:
            def __init__(self, s):
                self.st_size = s
                self.st_mode = real_stat.st_mode

        def fake_stat(self_path):
            return MockStat(sizes.pop(0) if sizes else 200)

        with patch.object(Path, "stat", fake_stat):
            assert is_file_stable(growing, delay_sec=0.01) is False

    def test_is_file_stable_valid_file(self, tmp_path):
        valid = tmp_path / "ready.wav"
        valid.write_bytes(b"complete audio payload data")
        assert is_file_stable(valid, delay_sec=0.05) is True

    def test_check_disk_space_healthy_and_low(self, tmp_path):
        # 1. Healthy: 50 GB free, threshold 3.0 GB
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = type("Usage", (), {"free": 50 * (1024**3)})()
            ok, free_gb = check_disk_space(tmp_path, min_free_gb=3.0)
            assert ok is True
            assert free_gb == 50.0

        # 2. Low disk: 1.5 GB free, threshold 3.0 GB
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = type("Usage", (), {"free": int(1.5 * (1024**3))})()
            ok, free_gb = check_disk_space(tmp_path, min_free_gb=3.0)
            assert ok is False
            assert free_gb == 1.5

        # 3. Exception in disk_usage falls back to (True, 999.0)
        with patch("shutil.disk_usage", side_effect=OSError("Drive inaccessible")):
            ok, free_gb = check_disk_space(tmp_path, min_free_gb=3.0)
            assert ok is True
            assert free_gb == 999.0
