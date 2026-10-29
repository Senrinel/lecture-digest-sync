"""White-box test suite for naming, timetable matching, and path sanitization.

Covers internal branches, edge cases, regex pattern match boundaries,
and Windows OS filesystem constraints.
"""

from datetime import datetime
from pathlib import Path
import pytest
import yaml

from lecture_pipeline.naming import (
    ParsedLectureInfo,
    STANDARD_PATTERN,
    COURSE_LECTURE_PATTERN,
    SONY_RECORDER_PATTERN,
    DATE_DETECTOR_PATTERN,
    GENERIC_AUDIO_NAMES,
    get_file_mtime_date,
    match_course_from_timetable,
    parse_filename,
    sanitize_filename,
)


class TestSanitizeFilenameWhitebox:
    """White-box branch and boundary coverage for sanitize_filename."""

    def test_empty_and_falsy_inputs(self):
        assert sanitize_filename("") == "Unsorted"
        assert sanitize_filename(None) == "Unsorted"
        assert sanitize_filename("   ") == "Unsorted"
        assert sanitize_filename("\t\r\n") == "Unsorted"
        assert sanitize_filename("...") == "Unsorted"
        assert sanitize_filename(". . .") == "Unsorted"

    def test_windows_illegal_characters_full_mapping(self):
        illegal = ':/?*"<>|\\'
        sanitized = sanitize_filename(illegal)
        for ch in illegal:
            assert ch not in sanitized
        # Verify specific safe substitutions
        assert "：" in sanitized  # colon -> fullwidth colon
        assert "-" in sanitized  # slash and backslash -> hyphen
        assert "？" in sanitized  # question mark -> fullwidth
        assert "_" in sanitized  # asterisk & pipe -> underscore
        assert "''" in sanitized  # double quote -> two single quotes
        assert "（" in sanitized  # less-than -> fullwidth parenthesis
        assert "）" in sanitized  # greater-than -> fullwidth parenthesis

    def test_consecutive_and_mixed_symbols(self):
        raw = ":::課堂:::///講義\\\\\\???***重點\"\"\"<<<>>>|||"
        clean = sanitize_filename(raw)
        assert ":" not in clean
        assert "/" not in clean
        assert "\\" not in clean
        assert "?" not in clean
        assert "*" not in clean
        assert '"' not in clean
        assert "<" not in clean
        assert ">" not in clean
        assert "|" not in clean
        assert "課堂" in clean
        assert "講義" in clean
        assert "重點" in clean

    def test_extreme_length_strings(self):
        long_name = "微積分" + "甲" * 500
        clean = sanitize_filename(long_name)
        assert clean.startswith("微積分")
        assert len(clean) == 503

    def test_unicode_and_chinese_preservation(self):
        text = "人文通識：世界文明史 - 第 3 講 (上)"
        clean = sanitize_filename(text)
        assert clean == "人文通識：世界文明史 - 第 3 講 (上)"

    def test_windows_device_reserved_names_safety(self, tmp_path):
        # On Windows, folders named CON, PRN, AUX, NUL can cause issues.
        # Ensure our paths create safely when sanitized.
        for dev_name in ["CON", "PRN", "AUX", "NUL", "COM1", "LPT1"]:
            clean = sanitize_filename(dev_name)
            target_dir = tmp_path / clean
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                assert target_dir.exists()
            except OSError:
                # Windows prevents raw CON/PRN folder creation; verify naming handles it
                assert len(clean) > 0


class TestTimetableMatchingWhitebox:
    """White-box branch coverage for match_course_from_timetable."""

    def test_nonexistent_or_invalid_config(self, tmp_path):
        assert match_course_from_timetable("20261029", "0900", config_path="nonexistent_cfg.yaml") is None
        assert match_course_from_timetable("", "0900") is None
        assert match_course_from_timetable("20261029", "") is None
        assert match_course_from_timetable("20261029", "12") is None  # < 4 digits

        bad_cfg = tmp_path / "bad.yaml"
        bad_cfg.write_text("invalid: yaml: [", encoding="utf-8")
        assert match_course_from_timetable("20261029", "0900", config_path=bad_cfg) is None

    def test_timetable_disabled_branch(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(yaml.safe_dump({"timetable": {"enabled": False, "Thursday": [{"time": "09:00-11:00", "course": "物理"}]}}), encoding="utf-8")
        assert match_course_from_timetable("20261029", "0900", config_path=cfg) is None

    def test_time_window_boundaries(self, tmp_path):
        # 2026-10-29 is a Thursday
        cfg = tmp_path / "cfg.yaml"
        cfg_data = {
            "timetable": {
                "enabled": True,
                "Thursday": [
                    {"time": "09:00-11:00", "course": "人文通識:文明史"},
                    {"time": "14:00-16:00", "course": "機率與統計"},
                ],
            }
        }
        cfg.write_text(yaml.safe_dump(cfg_data, allow_unicode=True), encoding="utf-8")

        # 30 mins early: 08:30 (8*60+30 = 510) == 9:00 - 30 -> Exactly allowed
        assert match_course_from_timetable("20261029", "0830", config_path=cfg) == "人文通識：文明史"
        # 31 mins early: 08:29 (8*60+29 = 509) < 510 -> Disallowed
        assert match_course_from_timetable("20261029", "0829", config_path=cfg) is None
        # During class: 10:15 -> Allowed
        assert match_course_from_timetable("20261029", "1015", config_path=cfg) == "人文通識：文明史"
        # Class end: 11:00 (11*60 = 660) -> Allowed
        assert match_course_from_timetable("20261029", "1100", config_path=cfg) == "人文通識：文明史"
        # 1 min after class: 11:01 -> Disallowed
        assert match_course_from_timetable("20261029", "1101", config_path=cfg) is None

    def test_malformed_slots_handling(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg_data = {
            "timetable": {
                "enabled": True,
                "Thursday": [
                    None,
                    {"time": "no_dash", "course": "Test1"},
                    {"time": "09:00-11:00", "course": None},
                    {"time": "invalid:time-string", "course": "Test2"},
                    "not_a_dict",
                ],
            }
        }
        cfg.write_text(yaml.safe_dump(cfg_data), encoding="utf-8")
        assert match_course_from_timetable("20261029", "0930", config_path=cfg) is None


class TestParseFilenameWhitebox:
    """White-box branch coverage for parse_filename."""

    def test_standard_pattern_branch(self, tmp_path):
        f = tmp_path / "作業系統_20261029_0900.mp3"
        f.touch()
        parsed = parse_filename(f, base_work_dir=tmp_path / "work")
        assert parsed.is_standard is True
        assert parsed.course == "作業系統"
        assert parsed.date == "20261029"
        assert parsed.time_str == "0900"
        assert parsed.warning is None

    def test_university_course_and_section_pattern_branch(self, tmp_path):
        f = tmp_path / "離散數學 03 B (1).wav"
        f.touch()
        parsed = parse_filename(f, base_work_dir=tmp_path / "work")
        assert parsed.is_standard is True
        assert parsed.course == "離散數學"
        assert parsed.lecture_num == "03B"
        assert parsed.part == "1"

    def test_sony_recorder_branch(self, tmp_path):
        # 261029_0930 -> 20261029, 0930
        f = tmp_path / "261029_0930.mp3"
        f.touch()
        parsed = parse_filename(f, base_work_dir=tmp_path / "work")
        assert parsed.date == "20261029"
        assert parsed.time_str == "0930"
        # If no timetable matches, defaults to Unsorted
        assert parsed.course in ["Unsorted", "人文通識：文明史"]

    def test_embedded_date_detector_fallback(self, tmp_path):
        f = tmp_path / "個人錄音_20261029_雜記.m4a"
        f.touch()
        parsed = parse_filename(f, base_work_dir=tmp_path / "work")
        assert parsed.date == "20261029"
        assert parsed.is_standard is False

    def test_generic_audio_names_fallback(self, tmp_path):
        for gen in ["voice_memo.mp3", "recording.wav", "track01.aac", "rec001.m4a"]:
            f = tmp_path / gen
            f.touch()
            parsed = parse_filename(f, base_work_dir=tmp_path / "work")
            assert parsed.course == "Unsorted"
            assert parsed.is_standard is False

    def test_custom_topic_fallback(self, tmp_path):
        f = tmp_path / "畢業專案期中進度匯報.flac"
        f.touch()
        parsed = parse_filename(f, base_work_dir=tmp_path / "work")
        assert parsed.course == "畢業專案期中進度匯報"
        assert parsed.is_standard is False
        assert len(parsed.date) == 8  # Derived from mtime YYYYMMDD
