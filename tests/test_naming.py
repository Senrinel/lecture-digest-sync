"""Unit tests for the naming and lecture routing module."""

from pathlib import Path
from lecture_pipeline.naming import parse_filename


def test_parse_standard_filename():
    """Verify parsing of standard CourseName_YYYYMMDD format."""
    info = parse_filename("DataMining_20261015.mp3", base_work_dir="work")
    assert info.is_standard is True
    assert info.course == "DataMining"
    assert info.date == "20261015"
    assert info.time_str is None
    assert info.warning is None
    assert info.work_dir.name == "20261015"
    assert info.work_dir.parent.name == "DataMining"


def test_parse_standard_filename_with_time():
    """Verify parsing of CourseName_YYYYMMDD_HHMM format."""
    info = parse_filename("Machine-Learning_20260920_0930.wav", base_work_dir="work")
    assert info.is_standard is True
    assert info.course == "Machine-Learning"
    assert info.date == "20260920"
    assert info.time_str == "0930"
    assert info.warning is None
    assert info.display_tag == "[Machine-Learning | 20260920_0930]"


def test_parse_university_course_with_lecture_number(tmp_path: Path):
    """Verify parsing of 'Discrete Mathematics 02.wav' pattern."""
    test_file = tmp_path / "Discrete Mathematics 02.wav"
    test_file.touch()

    info = parse_filename(test_file, base_work_dir="work")
    assert info.is_standard is True
    assert info.course == "Discrete Mathematics"
    assert info.lecture_num == "02"
    assert info.part is None
    assert "L02" in info.work_dir.name
    assert info.work_dir.parent.name == "Discrete Mathematics"


def test_parse_course_with_multiple_spaces_and_chinese(tmp_path: Path):
    """Verify parsing of 'Creative Invention  02.wav' and '通識 音樂 01.wav'."""
    f1 = tmp_path / "Creative Invention  02.wav"
    f1.touch()
    info1 = parse_filename(f1, base_work_dir="work")
    assert info1.course == "Creative Invention"
    assert info1.lecture_num == "02"

    f2 = tmp_path / "通識 音樂 01.wav"
    f2.touch()
    info2 = parse_filename(f2, base_work_dir="work")
    assert info2.course == "通識 音樂"
    assert info2.lecture_num == "01"


def test_parse_course_with_part_and_trailing_space(tmp_path: Path):
    """Verify parsing of '通識 音樂 04 (2).wav' and '通識 音樂 07 .wav'."""
    f1 = tmp_path / "通識 音樂 04 (2).wav"
    f1.touch()
    info1 = parse_filename(f1, base_work_dir="work")
    assert info1.course == "通識 音樂"
    assert info1.lecture_num == "04"
    assert info1.part == "2"

    f2 = tmp_path / "通識 音樂 07 .wav"
    f2.touch()
    info2 = parse_filename(f2, base_work_dir="work")
    assert info2.course == "通識 音樂"
    assert info2.lecture_num == "07"


def test_parse_sony_recorder_native_filename():
    """Verify parsing of SONY ICD-PX470 native format (YYMMDD_HHMM)."""
    info = parse_filename("261015_0930.mp3", base_work_dir="work")
    assert info.is_standard is False
    assert info.course == "Unsorted"
    assert info.date == "20261015"
    assert info.time_str == "0930"
    assert info.warning is not None
    assert "錄音筆原生" in info.warning
    assert info.work_dir.name == "20261015"
    assert info.work_dir.parent.name == "Unsorted"


def test_parse_irregular_filename_with_embedded_date(tmp_path: Path):
    """Verify fallback detection of embedded 8-digit date."""
    test_file = tmp_path / "Lecture_recording_20261105_final.m4a"
    test_file.touch()

    info = parse_filename(test_file, base_work_dir="work")
    assert info.is_standard is False
    assert info.course == "Unsorted"
    assert info.date == "20261105"
    assert info.warning is not None
    assert "20261105" in info.warning


def test_parse_completely_irregular_filename(tmp_path: Path):
    """Verify fallback when filename has no date information."""
    test_file = tmp_path / "voice_memo.mp3"
    test_file.touch()

    info = parse_filename(test_file, base_work_dir="work")
    assert info.is_standard is False
    assert info.course == "Unsorted"
    assert len(info.date) == 8  # YYYYMMDD from file timestamp
    assert info.warning is not None
