"""Inbox folder watcher and automated multi-part lecture pipeline executor."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional
from rich.console import Console

from lecture_pipeline.naming import ParsedLectureInfo, parse_filename
from lecture_pipeline.preprocess import preprocess_audio
from lecture_pipeline.state import PreprocessOptions, RunState, SegmentationOptions, save_run_state
from lecture_pipeline.stt import get_stt_channel, save_stt_outputs
from lecture_pipeline.vad_segment import segment_audio
from lecture_pipeline.cleanup.text_cleanup import GeminiTextCleanupEngine
from lecture_pipeline.cleanup.base import save_cleanup_outputs
from lecture_pipeline.summary.summarizer import GeminiLectureSummarizer
from lecture_pipeline.summary.base import save_summary_outputs
from lecture_pipeline.export.notebook_bundle import prepare_notebook_bundle
from lecture_pipeline.glossary import load_course_glossary

console = Console()

SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


def is_file_stable(filepath: Path, delay_sec: float = 2.0) -> bool:
    """Check if file size is stable (not actively being written/copied)."""
    try:
        size1 = filepath.stat().st_size
        time.sleep(delay_sec)
        size2 = filepath.stat().st_size
        return size1 == size2 and size1 > 0
    except OSError:
        return False


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
        if file_path.name.startswith("."):
            continue
        if file_path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            continue

        parsed = parse_filename(file_path)
        group_key = f"{parsed.course}__{parsed.date}__{parsed.lecture_num or 'single'}"

        # Determine part sort order
        part_idx = 1
        if parsed.part and parsed.part.isdigit():
            part_idx = int(parsed.part)
        elif "(2)" in file_path.name:
            part_idx = 2
        elif "(3)" in file_path.name:
            part_idx = 3

        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append((part_idx, file_path))

    # Sort each group by part index
    sorted_groups: Dict[str, List[Path]] = {}
    for g_key, items in groups.items():
        sorted_files = [p for _, p in sorted(items, key=lambda x: x[0])]
        sorted_groups[g_key] = sorted_files

    return sorted_groups


def run_pipeline_for_group(
    audio_files: List[Path],
    channel_id: str = "C7",
    pre_flags: Optional[str] = "hp,loud",
    cleanup_model: str = "gemini-3.1-flash-lite",
    summary_model: str = "gemini-3.1-flash-lite",
    archive_dir: Path | str = "archive",
) -> Path:
    """Execute complete end-to-end processing for a single or multi-part lecture."""
    start_total = time.time()
    primary_file = audio_files[0]
    parsed = parse_filename(primary_file)

    target_work_dir = parsed.work_dir
    target_work_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold cyan]🚀 自動化處理啟動：{parsed.display_tag}[/bold cyan]")
    if len(audio_files) > 1:
        console.print(f"📦 偵測到多段錄音分檔 (共 {len(audio_files)} 檔)，將自動合併：")
        for f in audio_files:
            console.print(f"   - {f.name}")

    # 1. Preprocess
    norm_audio = target_work_dir / "00_normalized.wav"
    pre_opts = PreprocessOptions.from_cli_flags(pre_flags) if pre_flags else PreprocessOptions()
    console.print("[dim]⏳ 執行音訊前處理與多段拼接...[/dim]")
    preprocess_audio(audio_files, norm_audio, opts=pre_opts)

    # 2. VAD Segmentation
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

    # 3. STT Transcription
    console.print(f"[dim]⏳ 執行語音轉錄 (通道 {channel_id})...[/dim]")
    glossary = load_course_glossary(parsed.course) if parsed.course != "Unsorted" else []
    channel = get_stt_channel(channel_id)
    transcription = channel.transcribe_all(chunks, course=parsed.course, date=parsed.date, glossary=glossary)
    save_stt_outputs(transcription, target_work_dir)
    state.mark_stage("stt")

    # 4. Cleanup
    console.print(f"[dim]⏳ 執行 AI 內文整理 ({cleanup_model})...[/dim]")
    cleaner = GeminiTextCleanupEngine(primary_model=cleanup_model)
    clean_result = cleaner.clean(transcription.full_text, course=parsed.course, date=parsed.date, glossary=glossary)
    save_cleanup_outputs(clean_result, target_work_dir)
    state.mark_stage("cleanup")

    # 5. Summary
    console.print(f"[dim]⏳ 執行 AI 重點總結 ({summary_model})...[/dim]")
    summarizer = GeminiLectureSummarizer(primary_model=summary_model)
    summary_result = summarizer.summarize(clean_result.full_cleaned_text, course=parsed.course, date=parsed.date, glossary=glossary)
    save_summary_outputs(summary_result, target_work_dir)
    state.mark_stage("summarize")

    # 6. Notebook Bundle
    console.print("[dim]⏳ 封裝 Google NotebookLM / Drive 複習包...[/dim]")
    bundle_info = prepare_notebook_bundle(target_work_dir)
    state.mark_stage("export")

    save_run_state(state, target_work_dir)

    # 7. Move processed files to archive
    target_archive = Path(archive_dir).resolve() / parsed.course
    target_archive.mkdir(parents=True, exist_ok=True)
    for f in audio_files:
        if f.is_file():
            shutil.move(str(f), str(target_archive / f.name))

    elapsed = round(time.time() - start_total, 2)
    console.print(f"[bold green]✨ {parsed.display_tag} 處理完成！耗時 {elapsed} 秒，檔案已歸檔至 {target_archive.name}/[/bold green]\n")
    return target_work_dir


def watch_inbox(
    inbox_dir: Path | str = "inbox",
    channel_id: str = "C7",
    pre_flags: Optional[str] = "hp,loud",
    once: bool = False,
    poll_interval: int = 5,
) -> int:
    """Monitor inbox directory and process newly incoming lectures automatically."""
    inbox_path = Path(inbox_dir).resolve()
    console.print(f"\n[bold blue]👀 啟動 Inbox 監看服務：{inbox_path}[/bold blue]")
    console.print(f"📌 預設 STT 通道：[yellow]{channel_id}[/yellow] | 模式：{'單次掃描' if once else f'持續監看 (間隔 {poll_interval}s)'}")

    total_processed = 0

    while True:
        groups = scan_and_group_inbox(inbox_path)
        if groups:
            for g_key, files in groups.items():
                # Check if all files in the group are stable
                if all(is_file_stable(f, delay_sec=1.0) for f in files):
                    try:
                        run_pipeline_for_group(
                            audio_files=files,
                            channel_id=channel_id,
                            pre_flags=pre_flags,
                        )
                        total_processed += 1
                    except Exception as e:
                        console.print(f"[bold red]❌ 處理 {g_key} 失敗: {e}[/bold red]")
                else:
                    console.print(f"[yellow]⏳ 檔案仍在寫入/複製中，稍後重試：{g_key}[/yellow]")

        if once:
            break

        time.sleep(poll_interval)

    return total_processed
