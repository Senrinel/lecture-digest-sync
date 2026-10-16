"""Drag and Drop One-Click Execution Engine.

Accepts dragged single audio files, multiple audio files, or folders containing audio files.
Automatically scans folders, groups multi-part lecture files, and executes the complete
end-to-end pipeline (Prep -> STT -> Clean -> Summary -> Bundle -> Google Docs).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lecture_pipeline.export.drive_publisher import GoogleDrivePublisher
from lecture_pipeline.naming import parse_filename
from lecture_pipeline.state import load_run_state
from lecture_pipeline.cli import (
    clean_command,
    notebook_command,
    prep_command,
    stt_command,
    summary_command,
)

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

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


def collect_audio_files(input_paths: List[Path | str]) -> List[Path]:
    """Recursively collect and validate all supported audio files from input files and folders."""
    found_files: List[Path] = []
    seen_paths = set()

    for item in input_paths:
        p = Path(item).resolve()
        if not p.exists():
            continue

        if p.is_file():
            if p.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS and p not in seen_paths:
                seen_paths.add(p)
                found_files.append(p)
        elif p.is_dir():
            # Scan directory for audio files
            for entry in sorted(p.rglob("*")):
                if entry.is_file() and entry.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
                    if entry not in seen_paths:
                        seen_paths.add(entry)
                        found_files.append(entry)

    return found_files


def group_audio_files(audio_files: List[Path]) -> Dict[str, List[Path]]:
    """Group audio files into discrete lectures (handling multi-part recordings).

    Returns:
        Dict mapping unique lecture key to sorted list of audio segments.
    """
    groups: Dict[str, List[tuple[int, Path]]] = {}

    for file_path in audio_files:
        parsed = parse_filename(file_path)
        group_key = f"{parsed.course}__{parsed.date}__{parsed.lecture_num or 'single'}"

        # Determine part sort order
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


def process_lecture_group(
    lecture_key: str,
    files: List[Path],
    channel: Optional[str] = None,
    upload: bool = True,
) -> Dict[str, str]:
    """Execute complete end-to-end pipeline for a single lecture group."""
    primary_file = files[0]
    parsed = parse_filename(primary_file)
    target_work_dir = parsed.work_dir
    target_work_dir.mkdir(parents=True, exist_ok=True)

    file_display = ", ".join(f.name for f in files)
    start_time = time.time()

    console.print(
        Panel(
            f"[bold green]課程主題[/bold green]：{parsed.course}\n"
            f"[bold green]課堂日期[/bold green]：{parsed.date}\n"
            f"[bold green]音訊檔案[/bold green]：{file_display} (共 {len(files)} 段)\n"
            f"[bold green]工作目錄[/bold green]：{target_work_dir}",
            title="🚀 正在處理課堂錄音",
            border_style="cyan",
        )
    )

    state = load_run_state(target_work_dir)

    # 1. Prep
    if state and state.stage_status.get("preprocess") == "DONE" and state.stage_status.get("vad_segment") == "DONE" and state.chunks:
        console.print(f"[dim]✔ 已完成 Phase 1 前處理與切段 ({len(state.chunks)} 切片)，自動沿用。[/dim]")
    else:
        prep_command(audio_files=files, pre=None, target_sec=None, work_dir=target_work_dir)
        state = load_run_state(target_work_dir)

    # 2. STT
    if state and state.stage_status.get("stt") == "DONE" and (target_work_dir / "01_raw.txt").is_file():
        console.print(f"[dim]✔ 已完成 Phase 2 STT 轉錄，自動沿用既有逐字稿以節省額度。[/dim]")
    else:
        stt_command(work_dir=target_work_dir, channel=channel, model=None)
        state = load_run_state(target_work_dir)

    # 3. Clean
    if state and state.stage_status.get("cleanup") == "DONE" and (target_work_dir / "02_cleaned.txt").is_file():
        console.print(f"[dim]✔ 已完成 Phase 3 內文整理，自動沿用既有整理文稿以節省額度。[/dim]")
    else:
        clean_command(
            work_dir=target_work_dir,
            model=None,
            prompt=None,
            min_ratio=None,
            max_ratio=None,
            chunk_chars=None,
            mode="text",
            fusion_file=None,
        )
        state = load_run_state(target_work_dir)

    # 4. Summary
    if state and state.stage_status.get("summarize") == "DONE" and (target_work_dir / "03_summary.md").is_file():
        console.print(f"[dim]✔ 已完成 Phase 4 重點總結，自動沿用既有筆記。[/dim]")
    else:
        summary_command(work_dir=target_work_dir, model=None, template=None, thinking=None)
        state = load_run_state(target_work_dir)

    # 5. Notebook Bundle
    notebook_command(work_dir=target_work_dir, export_dir=None, open_browser=False, reveal=False)

    # 6. Google Drive Auto-Upload
    folder_link = ""
    summary_doc_link = ""
    cleaned_doc_link = ""

    if upload and Path("credentials.json").is_file():
        try:
            publisher = GoogleDrivePublisher(credentials_file=Path("credentials.json"))
            pub_res = publisher.publish_lecture(target_work_dir)
            folder_link = pub_res.course_folder_link
            for doc in pub_res.docs:
                if "重點總結" in doc.title:
                    summary_doc_link = doc.web_link
                elif "完整課堂" in doc.title:
                    cleaned_doc_link = doc.web_link
        except Exception as e:
            console.print(f"[yellow]⚠️ Google Drive 發布暫時跳過 ({e})[/yellow]")

    elapsed = round(time.time() - start_time, 2)

    return {
        "course": parsed.course,
        "date": parsed.date,
        "duration": f"{elapsed:.1f}s",
        "folder_link": folder_link,
        "summary_doc_link": summary_doc_link,
        "cleaned_doc_link": cleaned_doc_link,
    }


def main():
    """Main CLI entry point for drag-and-drop execution."""
    raw_inputs = sys.argv[1:]

    console.print(
        Panel(
            "[bold cyan]課堂錄音自動化 ASR 與重點筆記系統[/bold cyan]\n"
            "支援單一音檔、多個音檔、或整份資料夾拖曳直接執行",
            title="🎙️ 拖曳一鍵執行模式 (Drag & Drop)",
            border_style="magenta",
        )
    )

    if not raw_inputs:
        console.print("\n[bold yellow]⚠️ 未偵測到輸入檔案或資料夾！[/bold yellow]")
        console.print("💡 [bold green]使用說明[/bold green]：")
        console.print("   1. 在檔案總管中選取【單個音檔】、【多段音檔】或【整個錄音資料夾】。")
        console.print("   2. 直接將選取的項目拖曳到此批次檔 (或桌面捷徑) 上放開。")
        console.print("   3. 系統將自動掃描音檔、智慧分組、轉錄整理並上傳 Google Docs！\n")
        input("請按 Enter 鍵關閉視窗...")
        return

    input_paths = [Path(p) for p in raw_inputs]
    audio_files = collect_audio_files(input_paths)

    if not audio_files:
        console.print(f"[bold red]❌ 所選項目中未找到支援的錄音檔案！[/bold red]")
        console.print(f"支援副檔名：{', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}\n")
        input("請按 Enter 鍵關閉視窗...")
        return

    # Group into lectures
    lecture_groups = group_audio_files(audio_files)

    console.print(f"📂 掃描完成：共偵測到 [bold green]{len(lecture_groups)}[/bold green] 堂課堂錄音 (共 [bold green]{len(audio_files)}[/bold green] 個音訊檔案)\n")

    results = []
    total_start = time.time()

    for idx, (group_key, files) in enumerate(lecture_groups.items(), start=1):
        console.print(f"\n[bold]━━━ 【進度 {idx}/{len(lecture_groups)}】 ━━━[/bold]")
        try:
            res = process_lecture_group(lecture_key=group_key, files=files)
            results.append(res)
        except Exception as e:
            console.print(f"[bold red]❌ 課堂處理失敗 ({group_key})：{e}[/bold red]")

    total_elapsed = round(time.time() - total_start, 2)

    # Final summary report
    console.print(f"\n[bold green]🎉 全數處理完畢！總耗時：{total_elapsed} 秒[/bold green]\n")

    table = Table(title="✨ 處理成果總覽表 (Summary Results)")
    table.add_column("課程名稱", style="cyan", justify="left")
    table.add_column("日期", style="green", justify="center")
    table.add_column("處理耗時", justify="right")
    table.add_column("重點筆記 Google Doc", style="bold blue")
    table.add_column("完整文稿 Google Doc", style="bold blue")

    for r in results:
        sum_link = f"[link={r['summary_doc_link']}]開啟重點筆記[/link]" if r['summary_doc_link'] else "無"
        clean_link = f"[link={r['cleaned_doc_link']}]開啟完整文稿[/link]" if r['cleaned_doc_link'] else "無"
        table.add_row(
            r["course"],
            r["date"],
            r["duration"],
            sum_link,
            clean_link,
        )

    console.print(table)
    console.print("\n[dim]提示：您可以點擊上方連結直接在瀏覽器開啟 Google Docs 編輯或於 NotebookLM 引用。[/dim]\n")
    input("請按 Enter 鍵結束...")


if __name__ == "__main__":
    main()
