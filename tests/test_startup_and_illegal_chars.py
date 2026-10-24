"""Tests for startup arguments handling and Windows illegal path characters handling."""

import os
import sys
from pathlib import Path
import pytest

from lecture_pipeline.naming import parse_filename, sanitize_filename, match_course_from_timetable
from lecture_pipeline.automation.watcher import DeadLetterQuarantine


def test_sanitize_filename():
    assert sanitize_filename("人文通識:世界文明史") == "人文通識：世界文明史"
    assert sanitize_filename("人文通識:藝術欣賞") == "人文通識：藝術欣賞"
    assert sanitize_filename("課堂/簡報\\重點?*\"<>|") == "課堂-簡報-重點？_''（）_"
    assert sanitize_filename("   科目名稱.  ") == "科目名稱"
    assert sanitize_filename("") == "Unsorted"


def test_match_course_and_parse_filename_colon(tmp_path):
    # Create a mock config with colons in timetable
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """
timetable:
  enabled: true
  Tuesday:
  - time: 9:00-11:00
    course: 人文通識:世界文明史
  - time: 11:00-13:00
    course: 人文通識:藝術欣賞
""",
        encoding="utf-8",
    )

    matched = match_course_from_timetable("20260929", "0914", config_path=cfg_file)
    assert matched == "人文通識：世界文明史"
    assert ":" not in matched

    # Verify parsed work_dir
    audio_path = tmp_path / "260929_0914.mp3"
    audio_path.touch()

    parsed = parse_filename(audio_path, base_work_dir=tmp_path / "work")
    # Even if timetable in default config is used, parsed.course must not have illegal colon
    assert ":" not in parsed.course
    assert ":" not in str(parsed.work_dir.name)
    # Ensure Windows can create the directory without WinError 123
    parsed.work_dir.mkdir(parents=True, exist_ok=True)
    assert parsed.work_dir.exists()


def test_dead_letter_quarantine_skips_path_error(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    audio = inbox / "test.mp3"
    audio.write_bytes(b"dummy")

    dl = DeadLetterQuarantine(inbox, max_retries=3)

    # Simulate WinError 123 OSError
    win_err = OSError("檔案名稱、目錄名稱或磁碟區標籤語法錯誤。")
    setattr(win_err, "winerror", 123)

    for _ in range(5):
        quarantined = dl.record_failure([audio], exc=win_err)
        assert not quarantined
        assert audio.is_file()
        assert not (inbox / "failed" / "test.mp3").exists()


def test_main_gui_flags_detection():
    # Simulate the logic in main.py for detecting GUI invocation
    known_cli_commands = {
        "prep", "stt", "clean", "summary", "bundle", "export",
        "bench", "autostart", "status", "watch", "env-check", "eval",
    }
    
    def check_is_gui(args):
        is_cli_subcommand = len(args) >= 1 and args[0].lower() in known_cli_commands
        is_gui_flag = any(
            a in ("--mode", "-m", "--host", "-h", "--port", "-p", "--debug", "-d")
            or a.startswith(("--mode=", "--host=", "--port="))
            for a in args
        )
        return (
            not args
            or (len(args) >= 1 and args[0] in ("gui", "-g", "--gui"))
            or (is_gui_flag and not is_cli_subcommand)
        )

    # 1. Autostart shortcut: --mode web
    assert check_is_gui(["--mode", "web"]) is True
    assert check_is_gui(["-m", "web"]) is True
    assert check_is_gui(["--mode=web"]) is True

    # 2. Double-click: no args
    assert check_is_gui([]) is True

    # 3. Explicit GUI
    assert check_is_gui(["gui"]) is True
    assert check_is_gui(["gui", "--mode", "window"]) is True

    # 4. Normal CLI
    assert check_is_gui(["prep", "audio.mp3"]) is False
    assert check_is_gui(["stt", "audio.mp3"]) is False
    assert check_is_gui(["summary", "audio.mp3"]) is False
