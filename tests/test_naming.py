from pathlib import Path
from lecture_pipeline.naming import parse_audio_filename


def test_standard_filename():
    p = Path("MachineLearning_20261015.mp3")
    info = parse_audio_filename(p)
    assert info.course_name == "MachineLearning"
    assert info.date_str == "20261015"
    assert info.time_str == ""
    assert info.is_standard_name is True
    assert info.canonical_id == "MachineLearning/20261015"


def test_standard_filename_with_time():
    p = Path("DataStructures_20261015_0930.wav")
    info = parse_audio_filename(p)
    assert info.course_name == "DataStructures"
    assert info.date_str == "20261015"
    assert info.time_str == "0930"
    assert info.is_standard_name is True
    assert info.canonical_id == "DataStructures/20261015_0930"


def test_sony_px470_recorder_raw_filename():
    p = Path("261015_0930.mp3")
    info = parse_audio_filename(p)
    assert info.course_name == "Unsorted"
    assert info.date_str == "20261015"
    assert info.time_str == "0930"
    assert info.is_standard_name is False
    assert "Unsorted" in info.warning


def test_fallback_filename(tmp_path):
    p = tmp_path / "lecture-recording-final.wav"
    p.touch()
    info = parse_audio_filename(p)
    assert info.is_standard_name is False
    assert len(info.date_str) == 8
