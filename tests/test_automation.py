"""Unit tests for P8 Automation and Watcher module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lecture_pipeline.automation import (
    QuotaManager,
    is_file_stable,
    scan_and_group_inbox,
)


def test_scan_and_group_inbox(tmp_path: Path):
    """Verify scanning inbox groups multi-part lecture files in correct order."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    f1 = inbox / "Discrete Mathematics 02.wav"
    f2 = inbox / "通識 音樂 04.wav"
    f3 = inbox / "通識 音樂 04 (2).wav"
    f4 = inbox / "ignore_readme.txt"

    for f in [f1, f2, f3, f4]:
        f.touch()

    groups = scan_and_group_inbox(inbox)

    assert len(groups) == 2
    # Verify Discrete Mathematics group
    dm_keys = [k for k in groups if "Discrete Mathematics" in k]
    assert len(dm_keys) == 1
    assert len(groups[dm_keys[0]]) == 1

    # Verify 通識 音樂 group has 2 parts sorted
    music_keys = [k for k in groups if "通識 音樂" in k]
    assert len(music_keys) == 1
    music_files = groups[music_keys[0]]
    assert len(music_files) == 2
    assert music_files[0].name == "通識 音樂 04.wav"
    assert music_files[1].name == "通識 音樂 04 (2).wav"


def test_is_file_stable(tmp_path: Path):
    """Verify file stability check."""
    test_f = tmp_path / "stable_test.wav"
    test_f.write_bytes(b"content")
    assert is_file_stable(test_f, delay_sec=0.1) is True


def test_quota_manager_policies(tmp_path: Path):
    """Verify quota manager fallback and deferred task writing."""
    qm = QuotaManager(policy="local_then_defer", local_fallback_channel="C7")
    fb = qm.handle_stt_quota_error(requested_channel="C1")
    assert fb == "C7"

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    d_file = qm.defer_task("cleanup", work_dir, "離散數學", "20261015")
    assert d_file.is_file()
    assert "cleanup" in d_file.read_text(encoding="utf-8")
