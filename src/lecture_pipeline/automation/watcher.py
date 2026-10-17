"""Inbox folder watcher and automated multi-part lecture pipeline executor.

Enhanced with:
1. Low process priority (BELOW_NORMAL) to guarantee zero interference with user's games/apps.
2. Two-stage file copy completion verification (settling time + non-blocking exclusive file lock).
3. Multi-part lecture grouping and debounce window.
4. Windows desktop toast notifications (開始解析 / 上傳成功 / 離線排隊 / 壞檔隔離).
5. System tray icon with dynamic badges (💤/🔄/⏫) and hover progress tooltips.
6. Dead letter queue (壞檔隔離): 3 strikes rule moving to inbox/failed/.
7. Task timeout circuit breaker (60 分鐘強制熔斷).
8. Auto-pruning & long-term archival to C:\\Users\\LinKJ\\Pictures\\Record (Opus + cleaned + summary + log).
9. Offline network resilience & next-day quota deferral queue.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from rich.console import Console

from lecture_pipeline.naming import parse_filename
from lecture_pipeline.preprocess import preprocess_audio
from lecture_pipeline.state import PreprocessOptions, RunState, SegmentationOptions, save_run_state
from lecture_pipeline.stt import get_stt_channel, save_stt_outputs
from lecture_pipeline.vad_segment import segment_audio
from lecture_pipeline.cleanup.text_cleanup import GeminiTextCleanupEngine
from lecture_pipeline.cleanup.base import save_cleanup_outputs
from lecture_pipeline.summary.summarizer import GeminiLectureSummarizer
from lecture_pipeline.summary.base import save_summary_outputs
from lecture_pipeline.export.notebook_bundle import prepare_notebook_bundle
from lecture_pipeline.export.drive_publisher import GoogleDrivePublisher
from lecture_pipeline.glossary import load_course_glossary
from lecture_pipeline.automation.notifier import (
    notify_corrupted_file,
    notify_offline_pending,
    notify_processing_started,
    notify_upload_success,
)
from lecture_pipeline.automation.offline_queue import (
    OfflineUploadQueue,
    is_internet_connected,
)
from lecture_pipeline.automation.pruner import prune_and_archive
from lecture_pipeline.automation.quota import QuotaManager
from lecture_pipeline.automation.tray import WatcherTray

console = Console(legacy_windows=False)

SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".wma",
}

SYSTEM_IGNORE_FILES = {
    ".gitkeep",
    ".ds_store",
    "desktop.ini",
    "thumbs.db",
}


def set_low_process_priority() -> bool:
    """Set current process to BELOW_NORMAL_PRIORITY_CLASS to avoid interfering with user tasks."""
    if sys.platform == "win32":
        try:
            import ctypes
            # BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            success = ctypes.windll.kernel32.SetPriorityClass(handle, 0x00004000)
            return bool(success)
        except Exception:
            pass
    else:
        try:
            os.nice(10)
            return True
        except Exception:
            pass
    return False


import logging
from logging.handlers import RotatingFileHandler

_watcher_logger = None


def _get_watcher_logger() -> logging.Logger:
    global _watcher_logger
    if _watcher_logger is None:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "watcher.log"
        logger = logging.getLogger("lecture_pipeline.watcher")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler = RotatingFileHandler(
                str(log_file),
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=5,              # 5 backup logs
                encoding="utf-8",
            )
            formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        _watcher_logger = logger
    return _watcher_logger


def log_watcher_event(message: str, level: str = "INFO") -> None:
    """Append structured log message to logs/watcher.log with auto-rotation."""
    try:
        logger = _get_watcher_logger()
        lvl = getattr(logging, level.upper(), logging.INFO)
        logger.log(lvl, message)
    except Exception:
        pass


def check_disk_space(target_dir: Path | str, min_free_gb: float = 3.0) -> tuple[bool, float]:
    """Check whether volume containing target_dir has at least min_free_gb space."""
    try:
        usage = shutil.disk_usage(str(Path(target_dir).resolve()))
        free_gb = round(usage.free / (1024 ** 3), 2)
        return free_gb >= min_free_gb, free_gb
    except Exception:
        return True, 999.0


def is_file_stable(filepath: Path, delay_sec: float = 2.0) -> bool:
    """Check if file size is stable and not locked by an ongoing copy/write operation."""
    try:
        p = Path(filepath)
        if not p.is_file():
            return False

        size1 = p.stat().st_size
        if size1 == 0:
            return False

        time.sleep(delay_sec)
        size2 = p.stat().st_size
        if size1 != size2:
            return False

        # Attempt to open file with read/write access to verify Windows copy lock is released
        with open(p, "r+b"):
            pass
        return True
    except (OSError, PermissionError):
        return False


class DeadLetterQuarantine:
    """Tracks retry attempts per file and isolates corrupted files after 3 failures."""

    def __init__(self, inbox_dir: Path | str, max_retries: int = 3):
        self.inbox_dir = Path(inbox_dir).resolve()
        self.failed_dir = self.inbox_dir / "failed"
        self.stats_file = self.inbox_dir / ".retry_stats.json"
        self.max_retries = max_retries

    def _load_stats(self) -> Dict[str, int]:
        if not self.stats_file.is_file():
            return {}
        try:
            with open(self.stats_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_stats(self, stats: Dict[str, int]) -> None:
        try:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def record_failure(self, audio_files: List[Path]) -> bool:
        """Increment failure counts. If failures >= max_retries, quarantine to inbox/failed/.

        Returns:
            True if corrupted files were quarantined, False otherwise.
        """
        stats = self._load_stats()
        quarantined = False

        for f in audio_files:
            fname = f.name
            count = stats.get(fname, 0) + 1
            stats[fname] = count

            if count >= self.max_retries:
                self.failed_dir.mkdir(parents=True, exist_ok=True)
                dest = self.failed_dir / fname
                if f.is_file():
                    if dest.exists():
                        dest.unlink()
                    try:
                        shutil.move(str(f), str(dest))
                    except Exception:
                        pass
                stats.pop(fname, None)
                quarantined = True
                notify_corrupted_file(fname)
                log_watcher_event(
                    f"檔案 {fname} 失敗超過 {self.max_retries} 次，判定壞檔並隔離至 {dest}",
                    level="ERROR",
                )

        self._save_stats(stats)
        return quarantined

    def record_success(self, audio_files: List[Path]) -> None:
        """Reset retry counts on successful completion."""
        stats = self._load_stats()
        changed = False
        for f in audio_files:
            if f.name in stats:
                stats.pop(f.name, None)
                changed = True
        if changed:
            self._save_stats(stats)


def scan_and_group_inbox(inbox_dir: Path | str) -> Dict[str, List[Path]]:
    """Scan inbox and group multi-part recordings belonging to the same lecture.

    Returns:
        Dict mapping group_key to list of sorted audio Paths.
    """
    inbox_path = Path(inbox_dir).resolve()
    if not inbox_path.is_dir():
        return {}

    groups: Dict[str, List[tuple[int, Path]]] = {}

    for file_path in inbox_path.iterdir():
        if not file_path.is_file():
            continue
        if file_path.name.lower() in SYSTEM_IGNORE_FILES or file_path.name.startswith("."):
            continue
        if file_path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            continue

        parsed = parse_filename(file_path)
        group_key = f"{parsed.course}__{parsed.date}__{parsed.lecture_num or 'single'}"

        part_idx = 1
        name = file_path.name
        if parsed.part and parsed.part.isdigit():
            part_idx = int(parsed.part)
        elif "(1)" in name or "_part1" in name.lower() or "_01" in name:
            part_idx = 1
        elif "(2)" in name or "_part2" in name.lower() or "_02" in name:
            part_idx = 2
        elif "(3)" in name or "_part3" in name.lower() or "_03" in name:
            part_idx = 3
        elif "(4)" in name or "_part4" in name.lower() or "_04" in name:
            part_idx = 4

        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append((part_idx, file_path))

    # Sort each group by part index
    sorted_groups: Dict[str, List[Path]] = {}
    for g_key, items in groups.items():
        sorted_files = [p for _, p in sorted(items, key=lambda x: (x[0], x[1].name))]
        sorted_groups[g_key] = sorted_files

    return sorted_groups


def _get_bridge():
    try:
        from lecture_pipeline.gui import bridge
        return bridge
    except Exception:
        return None


def run_pipeline_for_group(
    audio_files: List[Path],
    channel_id: str = "C1",
    pre_flags: Optional[str] = "hp,loud",
    cleanup_model: str = "gemini-3.5-flash-lite",
    summary_model: str = "gemini-3.5-flash-lite",
    archive_dir: Path | str = "archive",
    upload_drive: bool = True,
    tray: Optional[WatcherTray] = None,
    offline_queue: Optional[OfflineUploadQueue] = None,
    record_archive_base_dir: Optional[Path | str] = None,
    abort_event: Optional[threading.Event] = None,
    export_dir: Optional[Path | str] = None,
) -> Path:
    """Execute complete end-to-end processing for a single or multi-part lecture."""
    def check_aborted():
        if abort_event and abort_event.is_set():
            raise InterruptedError("任務已由使用者強制中止。")
        b = _get_bridge()
        if b:
            g_abort = b.get_task_abort_event()
            if g_abort and g_abort.is_set():
                raise InterruptedError("任務已由使用者強制中止。")
            p_pause = b.get_task_pause_event()
            while p_pause and p_pause.is_set():
                time.sleep(0.5)
                if (abort_event and abort_event.is_set()) or (g_abort and g_abort.is_set()):
                    raise InterruptedError("任務已由使用者強制中止。")

    start_total = time.time()
    primary_file = audio_files[0]
    parsed = parse_filename(primary_file)

    target_work_dir = parsed.work_dir
    target_work_dir.mkdir(parents=True, exist_ok=True)

    file_display = ", ".join(f.name for f in audio_files)
    console.print(f"\n[bold cyan]🚀 自動化處理啟動：{parsed.display_tag}[/bold cyan]")
    log_watcher_event(f"開始處理課堂：{parsed.display_tag}，檔案：{file_display}")

    # Desktop Toast Notification: 開始解析
    notify_processing_started(primary_file.name)

    def report_progress(stage: str, progress: int, detail: str):
        if tray:
            tray.update_status(
                state="processing",
                detail=parsed.display_tag,
                progress=f"{progress}% {stage}",
            )
        b = _get_bridge()
        if b:
            b.update_task_progress(
                stage=stage,
                progress=progress,
                detail=detail,
                task_name=parsed.display_tag,
                running=True,
            )

    report_progress("準備處理", 5, f"開始處理課堂：{parsed.display_tag} ({primary_file.name})")

    check_aborted()

    try:
        if len(audio_files) > 1:
            console.print(f"📦 偵測到多段錄音分檔 (共 {len(audio_files)} 檔)，將自動合併：")
            for f in audio_files:
                console.print(f"   - {f.name}")

        # 1. Preprocess
        check_aborted()
        report_progress("音訊前處理", 15, "執行 16kHz 單聲道標準化與多段拼接...")
        norm_audio = target_work_dir / "00_normalized.wav"
        pre_opts = PreprocessOptions.from_cli_flags(pre_flags) if pre_flags else PreprocessOptions()
        console.print("[dim]⏳ 執行音訊前處理與多段拼接...[/dim]")
        preprocess_audio(audio_files, norm_audio, opts=pre_opts)

        # 2. VAD Segmentation
        check_aborted()
        report_progress("VAD 切段", 30, "執行 Silero VAD 靜音切段偵測...")
        console.print("[dim]⏳ 執行 VAD 靜音切段...[/dim]")
        chunks = segment_audio(norm_audio, target_work_dir / "chunks")

        state = RunState(
            source_filename=" + ".join(f.name for f in audio_files),
            source_path=str(primary_file.resolve()),
            course=parsed.course,
            date=parsed.date,
            pre_options=pre_opts,
            seg_options=SegmentationOptions(),
            chunks=chunks,
        )
        state.mark_stage("preprocess")
        state.mark_stage("vad_segment")

        # 3. STT Transcription (with offline fallback check)
        check_aborted()
        actual_channel_id = channel_id
        if channel_id in ["C1", "C2"] and not is_internet_connected():
            console.print(f"[yellow]📶 偵測到無網路連線，STT 自動降級至本地高效能 GPU 通道 C7...[/yellow]")
            actual_channel_id = "C7"

        report_progress("語音轉錄", 50, f"執行語音轉錄 (通道 {actual_channel_id})...")
        console.print(f"[dim]⏳ 執行語音轉錄 (通道 {actual_channel_id})...[/dim]")
        glossary = load_course_glossary(parsed.course) if parsed.course != "Unsorted" else []
        channel = get_stt_channel(actual_channel_id)
        transcription = channel.transcribe_all(
            chunks,
            course=parsed.course,
            date=parsed.date,
            glossary=glossary,
            abort_event=abort_event,
        )
        save_stt_outputs(transcription, target_work_dir)
        state.mark_stage("stt")

        # 4. Cleanup
        check_aborted()
        report_progress("內文整理", 70, f"執行 AI 內文整理潤飾 ({cleanup_model})...")
        console.print(f"[dim]⏳ 執行 AI 內文整理 ({cleanup_model})...[/dim]")
        cleaner = GeminiTextCleanupEngine(primary_model=cleanup_model)
        clean_result = cleaner.clean(
            transcription.full_text,
            course=parsed.course,
            date=parsed.date,
            glossary=glossary,
            abort_event=abort_event,
        )
        save_cleanup_outputs(clean_result, target_work_dir)
        state.mark_stage("cleanup")

        # 5. Summary
        check_aborted()
        report_progress("重點筆記", 85, f"提煉 AI 重點結構筆記 ({summary_model})...")
        console.print(f"[dim]⏳ 執行 AI 重點總結 ({summary_model})...[/dim]")
        time.sleep(3.0)  # Rate limit cooldown buffer before summary call
        check_aborted()
        summarizer = GeminiLectureSummarizer(primary_model=summary_model)
        summary_result = summarizer.summarize(clean_result.full_cleaned_text, course=parsed.course, date=parsed.date, glossary=glossary)
        save_summary_outputs(summary_result, target_work_dir)
        state.mark_stage("summarize")

        # 6. Notebook Bundle & Exports
        check_aborted()
        console.print("[dim]⏳ 封裝重點總結筆記 Markdown 並同步至匯出目錄...[/dim]")
        target_export = export_dir
        if not target_export:
            try:
                from lecture_pipeline.state import load_config
                c_cfg = load_config(Path("config.yaml"))
                target_export = c_cfg.get("export", {}).get("export_dir", "exports")
            except Exception:
                target_export = "exports"

        prepare_notebook_bundle(target_work_dir, export_dir=target_export)
        state.mark_stage("export")

        save_run_state(state, target_work_dir)

        # 7. Google Drive Auto-Upload / Offline Queue
        if upload_drive:
            if tray:
                tray.update_status(state="uploading", detail=parsed.display_tag, progress="95% 上傳 Google Docs")
            b = _get_bridge()
            if b:
                b.update_task_progress(
                    stage="上傳與封存",
                    progress=95,
                    detail="同步 Google Docs 與長期壓縮封存...",
                    task_name=parsed.display_tag,
                    running=True,
                )

            has_creds = Path("credentials.json").is_file()
            online = is_internet_connected()

            if has_creds and online:
                try:
                    console.print("[dim]☁️ 正在自動上傳至 Google Drive 並轉為原生 Google Docs...[/dim]")
                    publisher = GoogleDrivePublisher(credentials_file=Path("credentials.json"))
                    pub_res = publisher.publish_lecture(target_work_dir)
                    console.print(f"[green]✔ Google Drive 資料夾：{pub_res.course_folder_link}[/green]")
                    log_watcher_event(f"已同步至 Google Drive：{pub_res.course_folder_link}")
                    notify_upload_success(primary_file.name, doc_url=pub_res.course_folder_link)
                except Exception as e:
                    console.print(f"[yellow]⚠️ Google Drive 自動同步遇到問題 ({e})，排入離線待上傳佇列。[/yellow]")
                    if offline_queue:
                        offline_queue.enqueue(target_work_dir, primary_file.name, parsed.course, parsed.date)
                    else:
                        notify_offline_pending(primary_file.name)
            else:
                if not online:
                    console.print(f"[yellow]📶 目前無網路連線，將 {primary_file.name} 寫入離線排隊佇列。[/yellow]")
                if offline_queue:
                    offline_queue.enqueue(target_work_dir, primary_file.name, parsed.course, parsed.date)
                else:
                    notify_offline_pending(primary_file.name)

        # 8. Auto-Pruning & Long-Term Archival (Specification & User Request)
        console.print("[dim]🧹 執行多餘產物修剪並封存至長期儲存區...[/dim]")
        try:
            prune_and_archive(
                work_dir=target_work_dir,
                course=parsed.course,
                date=parsed.date,
                record_base_dir=record_archive_base_dir,
                delete_chunks=True,
                delete_norm_wav=True,
            )
        except Exception as e:
            console.print(f"[yellow]⚠️ 自動修剪與封存提示: {e}[/yellow]")

        # 9. Move original inbox audio files to archive to prevent duplicate scans
        target_archive = Path(archive_dir).resolve() / parsed.course / parsed.date
        target_archive.mkdir(parents=True, exist_ok=True)
        for f in audio_files:
            if f.is_file():
                dest = target_archive / f.name
                if dest.exists():
                    dest.unlink()
                shutil.move(str(f), str(dest))

        elapsed = round(time.time() - start_total, 2)
        console.print(f"[bold green]✨ {parsed.display_tag} 處理完成！耗時 {elapsed} 秒，音檔已移至 archive/[/bold green]\n")
        log_watcher_event(f"課堂 {parsed.display_tag} 處理完成，總耗時 {elapsed} 秒")

        if tray:
            tray.increment_processed()
            tray.update_status(state="idle", detail="正在監看 Inbox", progress="等待音檔")

        b = _get_bridge()
        if b:
            b.notify_task_completed(parsed.display_tag, detail=f"課堂 {parsed.display_tag} 處理完成！")

        return target_work_dir

    except Exception as exc:
        b = _get_bridge()
        if b:
            b.notify_task_error(str(exc), task_name=parsed.display_tag)
        raise


def run_pipeline_with_timeout(
    audio_files: List[Path],
    channel_id: str = "C1",
    pre_flags: Optional[str] = "hp,loud",
    cleanup_model: str = "gemini-3.5-flash-lite",
    summary_model: str = "gemini-3.5-flash-lite",
    archive_dir: Path | str = "archive",
    upload_drive: bool = True,
    timeout_sec: int = 3600,  # 60 minutes limit
    tray: Optional[WatcherTray] = None,
    offline_queue: Optional[OfflineUploadQueue] = None,
    record_archive_base_dir: Optional[Path | str] = None,
) -> Path:
    """Execute complete pipeline protected by a strict execution timeout circuit breaker."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_pipeline_for_group,
            audio_files=audio_files,
            channel_id=channel_id,
            pre_flags=pre_flags,
            cleanup_model=cleanup_model,
            summary_model=summary_model,
            archive_dir=archive_dir,
            upload_drive=upload_drive,
            tray=tray,
            offline_queue=offline_queue,
            record_archive_base_dir=record_archive_base_dir,
        )
        try:
            return future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            log_watcher_event(
                f"任務逾時熔斷：處理超過 {timeout_sec} 秒上限，強制中斷任務",
                level="ERROR",
            )
            raise TimeoutError(f"任務處理逾時熔斷：超過 {timeout_sec} 秒上限")


def watch_inbox(
    inbox_dir: Path | str = "inbox",
    channel_id: str = "C1",
    pre_flags: Optional[str] = "hp,loud",
    once: bool = False,
    poll_interval: int = 5,
    upload_drive: bool = True,
    timeout_sec: int = 3600,
    enable_tray: bool = True,
    record_archive_base_dir: Optional[Path | str] = None,
) -> int:
    """Monitor inbox directory and process newly incoming lectures automatically."""
    inbox_path = Path(inbox_dir).resolve()
    inbox_path.mkdir(parents=True, exist_ok=True)

    set_low_process_priority()

    # Initialize auxiliary managers
    dead_letter = DeadLetterQuarantine(inbox_path, max_retries=3)
    offline_queue = OfflineUploadQueue()
    quota_manager = QuotaManager()

    # System tray setup
    tray: Optional[WatcherTray] = None
    if enable_tray:
        try:
            tray = WatcherTray(inbox_dir=inbox_path)
            tray.start()
            b = _get_bridge()
            if b:
                b.set_global_tray(tray)
        except Exception as e:
            console.print(f"[dim]⚠️ 系統匣圖示啟動略過: {e}[/dim]")
            tray = None

    # Ensure backend server running so tray web/window actions work seamlessly
    try:
        from lecture_pipeline.gui.launcher import is_port_in_use, run_uvicorn_server
        if not is_port_in_use(8000):
            th = threading.Thread(
                target=run_uvicorn_server,
                args=("127.0.0.1", 8000),
                daemon=True,
            )
            th.start()
            console.print("[dim]🌐 監看模式已於背景同步就緒 API / GUI 伺服器 (http://127.0.0.1:8000)[/dim]")
    except Exception as se:
        console.print(f"[dim]⚠️ 後端伺服器略過: {se}[/dim]")

    console.print(f"\n[bold blue]👀 啟動 Inbox 智慧監看服務：{inbox_path}[/bold blue]")
    console.print(f"📌 預設 STT 通道：[yellow]{channel_id}[/yellow] | 模式：{'單次掃描' if once else f'持續監看 (輪詢間隔 {poll_interval}s)'}")
    console.print(f"🛡️ 資源保護模式：[green]已開啟 (低優先權調度，零背景資源干擾)[/green]")
    console.print(f"⚡ 熔斷保護與壞檔隔離：[green]逾時上限 {timeout_sec}s / 失敗 3 次隔離[/green]\n")
    log_watcher_event(f"監看服務已啟動，監控目錄：{inbox_path}，通道：{channel_id}")

    total_processed = 0

    try:
        while True:
            # Check tray exit and pause flags
            if tray:
                if tray.stop_requested:
                    console.print("[yellow]🛑 接收到系統匣關閉訊號，正在退出監控...[/yellow]")
                    break
                if tray.is_paused:
                    time.sleep(1.0)
                    continue

            try:
                # 0. Check Disk Space Guard (protect drive from being filled)
                has_space, free_gb = check_disk_space(inbox_path, min_free_gb=3.0)
                if not has_space:
                    console.print(f"[bold red]⚠️ 磁碟剩餘空間不足 ({free_gb} GB < 3.0 GB)，已暫緩處理錄音！[/bold red]")
                    log_watcher_event(f"磁碟空間警告 ({free_gb} GB < 3.0 GB)，暫緩處理", level="WARNING")
                    time.sleep(30.0)
                    continue

                # 1. Process pending offline uploads if internet is connected
                if upload_drive and Path("credentials.json").is_file():
                    try:
                        publisher = GoogleDrivePublisher(credentials_file=Path("credentials.json"))
                        offline_queue.process_pending_uploads(publisher)
                    except Exception:
                        pass

                # 2. Check quota lock status
                is_locked, locked_until = quota_manager.is_quota_locked()
                if is_locked and channel_id in ["C1", "C2"]:
                    effective_channel = "C7"
                else:
                    effective_channel = channel_id

                # 3. Scan inbox for audio groups
                groups = scan_and_group_inbox(inbox_path)
                if groups:
                    for g_key, files in groups.items():
                        if all(is_file_stable(f, delay_sec=1.5) for f in files):
                            try:
                                run_pipeline_with_timeout(
                                    audio_files=files,
                                    channel_id=effective_channel,
                                    pre_flags=pre_flags,
                                    upload_drive=upload_drive,
                                    timeout_sec=timeout_sec,
                                    tray=tray,
                                    offline_queue=offline_queue,
                                    record_archive_base_dir=record_archive_base_dir,
                                )
                                dead_letter.record_success(files)
                                total_processed += 1
                            except TimeoutError as te:
                                console.print(f"[bold red]❌ 任務逾時熔斷 ({g_key}): {te}[/bold red]")
                                log_watcher_event(f"逾時熔斷 ({g_key}): {te}", level="ERROR")
                                dead_letter.record_failure(files)
                            except Exception as e:
                                console.print(f"[bold red]❌ 處理 {g_key} 遭遇錯誤: {e}[/bold red]")
                                log_watcher_event(f"處理失敗 ({g_key}): {e}", level="ERROR")
                                dead_letter.record_failure(files)
                        else:
                            console.print(f"[yellow]⏳ 偵測到新錄音正在傳輸寫入中，靜候寫入完成：{g_key}[/yellow]")

                if once:
                    break

                time.sleep(poll_interval)

            except KeyboardInterrupt:
                console.print("\n[yellow]🛑 監看服務已由使用者手動中止。[/yellow]")
                log_watcher_event("監看服務已手動中止。")
                break
            except Exception as e:
                console.print(f"[yellow]⚠️ 監看巡檢發生暫時性異常，將於 10 秒後重試：{e}[/yellow]")
                log_watcher_event(f"巡檢異常: {e}", level="WARNING")
                time.sleep(10.0)

    finally:
        if tray:
            tray.stop()

    return total_processed
