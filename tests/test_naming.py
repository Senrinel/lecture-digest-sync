"""Unit tests for naming.py."""

from pathlib import Path
from lecture_pipeline.naming import parse_audio_filename, get_work_directory


def test_standard_naming_format_1():
    meta = parse_audio_filename("DataMining_20261015.mp3")
    assert meta.course_name == "DataMining"
    assert meta.date_str == "2026-10-15"
    assert meta.is_standard is True
    assert meta.time_str is None
    assert meta.exception_note is None


def test_standard_naming_with_time():
    meta = parse_audio_filename("MachineLearning_20261015_0930.wav")
    assert meta.course_name == "MachineLearning"
    assert meta.date_str == "2026-10-15"
    assert meta.time_str == "0930"
    assert meta.is_standard is True


def test_standard_naming_with_dashes():
    meta = parse_audio_filename("OS-Course_2026-10-15_1400.m4a")
    assert meta.course_name == "OS-Course"
    assert meta.date_str == "2026-10-15"
    assert meta.time_str == "1400"
    assert meta.is_standard is True


def test_sony_recorder_raw_naming():
    # Sony ICD-PX470 typical filename: YYMMDD_HHMM.mp3
    meta = parse_audio_filename("261015_0930.mp3")
    assert meta.course_name == "Unsorted"
    assert meta.date_str == "2026-10-15"
    assert meta.time_str == "0930"
    assert meta.is_standard is False
    assert "Sony recorder" in meta.exception_note


def test_unknown_filename_fallback(tmp_path):
    # Completely random name
    fake_file = tmp_path / "recording_sample.mp3"
    fake_file.write_bytes(b"dummy audio")
    meta = parse_audio_filename(fake_file)
    assert meta.course_name == "Unsorted"
    assert meta.is_standard is False
    assert meta.exception_note is not None


def test_work_directory_routing():
    meta = parse_audio_filename("Algo_20261015.mp3")
    work_dir = get_work_directory("work", meta)
    assert work_dir == Path("work/Algo/2026-10-15")
