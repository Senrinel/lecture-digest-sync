"""CLI Entrypoint for the Lecture Pipeline.

Supports inspecting audio files, running preprocessing, and silence-based VAD chunking.
"""

from __future__ import annotations

import sys
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from lecture_pipeline.naming import parse_audio_filename, get_work_directory
from lecture_pipeline.preprocess import PreprocessOptions, preprocess_audio
from lecture_pipeline.vad_segment import get_audio_duration, segment_audio
from lecture_pipeline.state import RunState, save_run_state

app = typer.Typer(help="Lecture Pipeline CLI")
console = Console(legacy_windows=False)


@app.command()
def info(audio_path: str = typer.Argument(..., help="Path to audio file")):
    """Inspect an audio file, its parsed filename metadata, and duration."""
    path = Path(audio_path).resolve()
    if not path.exists():
        console.print(f"[red]Error: File not found: {path}[/red]")
        raise typer.Exit(1)

    meta = parse_audio_filename(path)
    try:
        dur = get_audio_duration(path)
        mins = int(dur // 60)
        secs = int(dur % 60)
        dur_str = f"{dur:.2f}s ({mins}m {secs}s)"
    except Exception as e:
        dur_str = f"Error reading duration: {e}"

    table = Table(title="音檔資訊與檔名解析")
    table.add_column("欄位", style="bold cyan")
    table.add_column("數值", style="green")

    table.add_row("原始檔案", meta.original_filename)
    table.add_row("完整路徑", meta.original_path)
    table.add_row("課程名稱", meta.course_name)
    table.add_row("課程日期", meta.date_str)
    table.add_row("上課時間標記", meta.time_str or "(無)")
    table.add_row("標準命名格式", "✅ 是" if meta.is_standard else "⚠️ 否 (例外分流)")
    if meta.exception_note:
        table.add_row("例外說明", meta.exception_note)
    table.add_row("音訊總時長", dur_str)

    console.print(table)


@app.command()
def prep(
    audio_path: str = typer.Argument(..., help="Path to audio file"),
    pre: str = typer.Option("hp,loud", help="Preprocessing switches, e.g., 'hp,loud' or 'hp:80Hz,loud:-19LUFS'"),
    target_sec: float = typer.Option(600.0, help="Target chunk duration in seconds (default: 600s = 10 mins)"),
    work_dir: str = typer.Option("work", help="Base directory for working files"),
):
    """Run Phase 1: Preprocess audio and segment into natural speech chunks."""
    path = Path(audio_path).resolve()
    if not path.exists():
        console.print(f"[red]Error: File not found: {path}[/red]")
        raise typer.Exit(1)

    meta = parse_audio_filename(path)
    dedicated_work_dir = get_work_directory(work_dir, meta)
    dedicated_work_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold cyan]🚀 開始處理音檔：{meta.original_filename}[/bold cyan]")
    console.print(f"📁 工作目錄：{dedicated_work_dir}")

    # 1. Parse preprocessing options
    opts = PreprocessOptions.from_string(pre)
    console.print(f"🎛️ 前處理設定：hp={opts.hp}, loud={opts.loud}, dyn={opts.dyn}, nr={opts.nr}")

    # 2. Run Preprocessing
    preprocessed_wav = dedicated_work_dir / "00_preprocessed.wav"
    console.print("⏳ 正在執行音訊前處理 (標準化為 16kHz Mono WAV)...")
    _, prep_elapsed = preprocess_audio(path, preprocessed_wav, opts)
    dur = get_audio_duration(preprocessed_wav)
    console.print(f"✅ 前處理完成！耗時 {prep_elapsed:.2f}s，音訊長度：{dur:.1f}s ({dur/60:.1f} 分鐘)")

    # 3. Run VAD & Segmentation
    console.print(f"⏳ 正在進行智慧 VAD 靜音切段 (目標片段: {target_sec/60:.1f} 分鐘)...")
    chunks = segment_audio(preprocessed_wav, dedicated_work_dir, target_chunk_sec=target_sec)
    console.print(f"✅ 切段完成！共切出 {len(chunks)} 個自然停頓片段。")

    # 4. Save Run State
    state = RunState(
        status="SEGMENTED",
        audio_meta=meta,
        preprocess_options=opts,
        total_duration_sec=dur,
        preprocessed_wav=str(preprocessed_wav),
        chunks=chunks,
    )
    save_run_state(dedicated_work_dir, state)

    # 5. Display Summary Table
    table = Table(title="Phase 1 執行結果摘要")
    table.add_column("片段", justify="center", style="bold yellow")
    table.add_column("檔名")
    table.add_column("開始時間 (s)", justify="right")
    table.add_column("結束時間 (s)", justify="right")
    table.add_column("片段長度 (s / min)", justify="right", style="green")

    for c in chunks:
        mins_str = f"{c.duration_sec:.1f}s ({c.duration_sec/60:.1f}m)"
        table.add_row(f"#{c.index:02d}", c.filename, f"{c.start_sec:.1f}", f"{c.end_sec:.1f}", mins_str)

    console.print(table)
    console.print(f"[bold green]🎉 Phase 1 處理完成！中介檔案與狀態已記錄至 {dedicated_work_dir / 'run.json'}[/bold green]\n")


if __name__ == "__main__":
    app()
