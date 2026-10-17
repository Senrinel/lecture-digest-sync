"""Unit tests for P8 Automation, Notifications, Tray, Quarantine, Timeout, Pruning, and Offline Quota."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lecture_pipeline.automation import (
    DeadLetterQuarantine,
    OfflineUploadQueue,
    QuotaManager,
    WatcherTray,
    create_status_icon,
    is_file_stable,
    notify_corrupted_file,
    notify_offline_pending,
    notify_processing_started,
    notify_upload_success,
    prune_and_archive,
    run_pipeline_with_timeout,
    scan_and_group_inbox,
)
from lecture_pipeline.glossary import auto_extract_course_glossary, extract_terms_from_text


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
    dm_keys = [k for k in groups if "Discrete Mathematics" in k]
    assert len(dm_keys) == 1
    assert len(groups[dm_keys[0]]) == 1

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


def test_dead_letter_quarantine_three_strikes(tmp_path: Path):
    """Verify corrupted file isolation after 3 consecutive failures."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    bad_audio = inbox / "Corrupted_Lecture_20261016.wav"
    bad_audio.write_bytes(b"bad audio bytes")

    quarantine = DeadLetterQuarantine(inbox, max_retries=3)

    with patch("lecture_pipeline.automation.watcher.notify_corrupted_file") as mock_notify:
        # Strike 1:
        res1 = quarantine.record_failure([bad_audio])
        assert res1 is False
        assert bad_audio.is_file()
        assert not (inbox / "failed" / bad_audio.name).exists()
        assert mock_notify.call_count == 0

        # Strike 2:
        res2 = quarantine.record_failure([bad_audio])
        assert res2 is False
        assert bad_audio.is_file()
        assert mock_notify.call_count == 0

        # Strike 3: Quarantine triggered!
        res3 = quarantine.record_failure([bad_audio])
        assert res3 is True
        assert not bad_audio.exists()
        quarantined_file = inbox / "failed" / bad_audio.name
        assert quarantined_file.is_file()
        assert mock_notify.call_count == 1
        mock_notify.assert_called_with("Corrupted_Lecture_20261016.wav")


def test_task_timeout_circuit_breaker(tmp_path: Path):
    """Verify execution timeout circuit breaker raises TimeoutError when exceeding threshold."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    audio = inbox / "Slow_Task_20261016.wav"
    audio.touch()

    def slow_mock(*args, **kwargs):
        time.sleep(0.5)

    with patch("lecture_pipeline.automation.watcher.run_pipeline_for_group", side_effect=slow_mock):
        with pytest.raises(TimeoutError) as exc_info:
            run_pipeline_with_timeout(
                audio_files=[audio],
                timeout_sec=0.1,  # Strict timeout
            )
        assert "任務處理逾時熔斷" in str(exc_info.value)


def test_auto_pruning_and_archival(tmp_path: Path):
    """Verify lifecycle pruning removes chunks and raw wav, preserving 4 essential files."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    record_dir = tmp_path / "Record"

    # Create dummy artifacts in work_dir
    norm_wav = work_dir / "00_normalized.wav"
    # Write a valid minimal wave header so compression logic can run or fallback safely
    import wave
    with wave.open(str(norm_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)

    cleaned_txt = work_dir / "02_cleaned.txt"
    cleaned_txt.write_text("課堂精修文稿內容", encoding="utf-8")

    summary_md = work_dir / "03_summary.md"
    summary_md.write_text("# 課堂重點總結", encoding="utf-8")

    run_json = work_dir / "run.json"
    run_json.write_text('{"run_id": "test_run"}', encoding="utf-8")

    chunks_dir = work_dir / "chunks"
    chunks_dir.mkdir()
    (chunks_dir / "chunk_001.wav").touch()

    proj_logs_dir = tmp_path / "logs"

    # Execute pruning and archival
    archived = prune_and_archive(
        work_dir=work_dir,
        course="演算法",
        date="20261016",
        record_base_dir=record_dir,
        project_logs_dir=proj_logs_dir,
        delete_chunks=True,
        delete_norm_wav=True,
    )

    # 1. Verify intermediate waste is pruned from work_dir
    assert not chunks_dir.exists()
    assert not norm_wav.exists()

    # 2. Verify media & notes are in record_dir, and log is in project logs dir
    dest_dir = record_dir / "演算法" / "20261016"
    assert dest_dir.is_dir()
    assert "audio" in archived
    assert archived["audio"].is_file()
    assert (dest_dir / "02_cleaned.txt").is_file()
    assert (dest_dir / "03_summary.md").is_file()
    # Specification compliance: archives must not contain HTML files
    assert not (dest_dir / "04_player.html").exists()
    assert not any(dest_dir.glob("*.html"))
    # User requested: log 改放至專案資料夾內 (不在 Record 中)
    assert not (dest_dir / "run.json").exists()
    assert "run_log" in archived
    assert (proj_logs_dir / "演算法" / "20261016" / "run.json").is_file()


def test_offline_upload_queue(tmp_path: Path):
    """Verify queuing when offline and uploading when network returns."""
    queue_dir = tmp_path / "pending_uploads"
    oq = OfflineUploadQueue(queue_dir=queue_dir)

    work_dir = tmp_path / "work"
    work_dir.mkdir()

    with patch("lecture_pipeline.automation.offline_queue.notify_offline_pending") as mock_offline_notify:
        oq.enqueue(
            work_dir=work_dir,
            display_name="演算法_20261016.wav",
            course="演算法",
            date="20261016",
        )
        assert mock_offline_notify.call_count == 1
        mock_offline_notify.assert_called_with("演算法_20261016.wav")

    pending = oq.get_pending()
    assert len(pending) == 1
    assert pending[0]["display_name"] == "演算法_20261016.wav"

    # Simulate online restoration
    fake_publisher = MagicMock()
    fake_pub_res = MagicMock()
    fake_pub_res.course_folder_link = "https://drive.google.com/test"
    fake_pub_res.doc_link = "https://docs.google.com/document/d/test"
    fake_publisher.publish_lecture.return_value = fake_pub_res

    with patch("lecture_pipeline.automation.offline_queue.is_internet_connected", return_value=True), \
         patch("lecture_pipeline.automation.offline_queue.notify_upload_success") as mock_success_notify:
        uploaded_count = oq.process_pending_uploads(fake_publisher)
        assert uploaded_count == 1
        assert len(oq.get_pending()) == 0
        mock_success_notify.assert_called_with("演算法_20261016.wav", doc_url="https://docs.google.com/document/d/test")


def test_quota_manager_lock_and_deferral(tmp_path: Path):
    """Verify QuotaManager next-day locking and deferred task writing."""
    lock_file = tmp_path / "quota_lock.json"
    qm = QuotaManager(lock_file=lock_file, daily_reset_hour_utc=8)

    assert qm.is_quota_locked()[0] is False

    reset_time = qm.mark_quota_exhausted(service="Gemini-C1", reason="429 ResourceExhausted")
    assert qm.is_quota_locked()[0] is True
    assert lock_file.is_file()

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    d_file = qm.defer_task("cleanup", work_dir, "離散數學", "20261015")
    assert d_file.is_file()
    content = d_file.read_text(encoding="utf-8")
    assert "resume_after" in content
    assert "離散數學" in content

    # Test unlock
    qm.unlock_quota()
    assert qm.is_quota_locked()[0] is False


def test_desktop_notifications():
    """Verify exact desktop notification strings required by user specification."""
    with patch("lecture_pipeline.automation.notifier.send_notification") as mock_send:
        notify_processing_started("課堂錄音01.wav", sync=True)
        mock_send.assert_called_with(
            title="課堂錄音處理",
            message='"課堂錄音01.wav" 已開始解析',
            sync=True,
        )

        notify_upload_success("課堂錄音01.wav", sync=True)
        mock_send.assert_called_with(
            title="Google 文件同步",
            message='"課堂錄音01.wav" 上傳成功',
            sync=True,
        )

        notify_offline_pending("課堂錄音01.wav", sync=True)
        mock_send.assert_called_with(
            title="離線排隊中",
            message='"課堂錄音01.wav" 處理完成，等候上傳',
            sync=True,
        )

        notify_corrupted_file("壞音檔.wav", sync=True)
        mock_send.assert_called_with(
            title="壞檔隔離警報",
            message='"壞音檔.wav" 損壞，放棄處理',
            sync=True,
        )


def test_system_tray_icon_and_menu(tmp_path: Path):
    """Verify system tray status icon generation and state transitions."""
    icon_path = Path("icon.png")
    idle_img = create_status_icon(icon_path, state="idle")
    proc_img = create_status_icon(icon_path, state="processing")
    upload_img = create_status_icon(icon_path, state="uploading")

    assert idle_img.size == (64, 64)
    assert proc_img.size == (64, 64)
    assert upload_img.size == (64, 64)

    tray = WatcherTray(inbox_dir=tmp_path / "inbox", log_file=tmp_path / "watcher.log")
    assert tray.current_state == "idle"

    tray.update_status(state="processing", detail="離散數學 02", progress="50% 轉錄中")
    assert tray.current_state == "processing"
    assert "離散數學 02" in tray.current_detail

    tooltip = tray._build_tooltip()
    assert "處理🔄" in tooltip
    assert "50% 轉錄中" in tooltip

    tray.increment_processed()
    assert tray.processed_count == 1
    assert "今日處理: 1 堂" in tray._build_tooltip()

    # Toggle pause
    tray._on_toggle_pause(None, None)
    assert tray.is_paused is True
    assert "已暫停⏸️" in tray._build_tooltip()
    assert tray._get_pause_label(None) == "恢復監控 ▶️"

    tray._on_toggle_pause(None, None)
    assert tray.is_paused is False
    assert tray._get_pause_label(None) == "暫停監控 ⏸️"


def test_glossary_pure_local_extraction(tmp_path: Path):
    """Verify pure-local extraction extracts acronyms, algorithms and bilingual terms without API."""
    text = """
    We introduce Support Vector Machine (支援向量機) and PCA dimensionality reduction.
    The teacher explains Dijkstra's Algorithm and Dynamic Programming (動態規劃).
    Using GPU and VRAM for FFT analysis with 梯度下降法 and 特徵值.
    """
    terms = extract_terms_from_text(text)
    assert "PCA" in terms
    assert "GPU" in terms
    assert "VRAM" in terms
    assert "FFT" in terms
    assert "Dynamic Programming" in terms
    assert "動態規劃" in terms
    assert "支援向量機" in terms

    # Test auto update course glossary
    course_dir = tmp_path / "courses"
    note = tmp_path / "lecture.txt"
    note.write_text(text, encoding="utf-8")

    extracted = auto_extract_course_glossary(
        course="機器學習",
        source_path=note,
        courses_dir=course_dir,
    )
    assert len(extracted) > 0
    glossary_file = course_dir / "機器學習" / "glossary.txt"
    assert glossary_file.is_file()
    glossary_content = glossary_file.read_text(encoding="utf-8")
    assert "Dynamic Programming" in glossary_content
    assert "動態規劃" in glossary_content


def test_autostart_enable_disable(tmp_path: Path):
    """Verify enabling and disabling Windows autostart script."""
    fake_startup = tmp_path / "Startup"
    fake_startup.mkdir()

    with patch("lecture_pipeline.automation.autostart.get_startup_dir", return_value=fake_startup):
        from lecture_pipeline.automation.autostart import (
            disable_autostart,
            enable_autostart,
            get_autostart_status,
        )

        status_before = get_autostart_status()
        assert status_before["enabled"] is False

        vbs = enable_autostart(project_dir=tmp_path, channel="C1")
        assert vbs.is_file()
        assert "LecturePipelineWatcher.vbs" in vbs.name
        content = vbs.read_text(encoding="utf-8")
        assert "uv run python -m lecture_pipeline.cli watch" in content

        status_after = get_autostart_status()
        assert status_after["enabled"] is True

        disabled = disable_autostart()
        assert disabled is True
        assert not vbs.is_file()
        assert get_autostart_status()["enabled"] is False


def test_drag_and_drop_collect_and_group(tmp_path: Path):
    """Verify drag_and_drop module collecting from nested folders and grouping multi-part lectures."""
    from lecture_pipeline.drag_and_drop import collect_audio_files, group_audio_files

    folder = tmp_path / "Recordings"
    subfolder = folder / "SubDir"
    subfolder.mkdir(parents=True)

    f1 = folder / "演算法_20261016.mp3"
    f2 = subfolder / "通識 音樂 02 (1).wav"
    f3 = subfolder / "通識 音樂 02 (2).wav"
    f4 = folder / "notes.txt"

    for f in [f1, f2, f3, f4]:
        f.touch()

    collected = collect_audio_files([folder])
    assert len(collected) == 3
    assert f4 not in collected

    grouped = group_audio_files(collected)
    assert len(grouped) == 2

    music_key = [k for k in grouped if "通識 音樂" in k][0]
    assert len(grouped[music_key]) == 2
    assert grouped[music_key][0].name == "通識 音樂 02 (1).wav"
    assert grouped[music_key][1].name == "通識 音樂 02 (2).wav"


def test_check_disk_space(tmp_path: Path):
    """Verify disk space checking function."""
    from lecture_pipeline.automation.watcher import check_disk_space

    has_space, free_gb = check_disk_space(tmp_path, min_free_gb=0.001)
    assert has_space is True
    assert free_gb > 0


def test_timetable_auto_matching(tmp_path: Path):
    """Verify automatic course matching from timetable based on weekday and time."""
    from lecture_pipeline.naming import match_course_from_timetable

    fake_config = tmp_path / "config.yaml"
    fake_config.write_text(
        """
timetable:
  enabled: true
  Thursday:
    - time: "10:00-12:00"
      course: "實驗室組會"
  Friday:
    - time: "09:00-12:00"
      course: "訊號與系統"
""",
        encoding="utf-8",
    )

    # 2026-10-15 is Thursday. 10:30 falls in 10:00-12:00
    matched = match_course_from_timetable("20261015", "1030", config_path=fake_config)
    assert matched == "實驗室組會"

    # 2026-10-16 is Friday. 09:15 falls in 09:00-12:00
    matched_fri = match_course_from_timetable("20261016", "0915", config_path=fake_config)
    assert matched_fri == "訊號與系統"

    # Unmatched time returns None
    assert match_course_from_timetable("20261016", "2300", config_path=fake_config) is None


def test_generate_interactive_player(tmp_path: Path):
    """Verify standalone audio-synced HTML player generation."""
    from lecture_pipeline.export.html_player import generate_interactive_player

    work_dir = tmp_path / "work"
    work_dir.mkdir()

    cleaned_txt = work_dir / "02_cleaned.txt"
    cleaned_txt.write_text("第一段課堂介紹。\n\n第二段推導細節。", encoding="utf-8")

    summary_md = work_dir / "03_summary.md"
    summary_md.write_text("# 重點整理筆記", encoding="utf-8")

    player_html = generate_interactive_player(
        work_dir=work_dir,
        course="訊號與系統",
        date="20261016",
    )

    assert player_html.is_file()
    content = player_html.read_text(encoding="utf-8")
    assert "訊號與系統 (20261016)" in content
    assert "第一段課堂介紹" in content
    assert "重點整理筆記" in content
    assert "audioPlayer" in content

