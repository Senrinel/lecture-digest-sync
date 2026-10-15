"""Command Line Interface for the Lecture ASR Pipeline."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from lecture_pipeline.glossary import load_course_glossary
from lecture_pipeline.naming import parse_filename
from lecture_pipeline.preprocess import preprocess_audio
from lecture_pipeline.state import (
    PreprocessOptions,
    RunState,
    SegmentationOptions,
    load_config,
    load_run_state,
    save_run_state,
)
from lecture_pipeline.stt import get_stt_channel, save_stt_outputs
from lecture_pipeline.vad_segment import segment_audio


if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

app = typer.Typer(
    name="lecture-pipeline",
    help="自動化大學課堂錄音 ASR 語音辨識與重點筆記整理系統",
    add_completion=False,
)
console = Console(legacy_windows=False)


@app.command("prep")
def prep_command(
    audio_file: Path = typer.Argument(..., help="欲處理的錄音檔案路徑 (.wav, .mp3, .m4a 等)"),
    pre: Optional[str] = typer.Option(
        None,
        "--pre",
        "-p",
        help="前處理開關組合，以逗號分隔，例如 'hp,loud' 或 'hp=120Hz,loud=-16LUFS,dyn=light'",
    ),
    target_sec: Optional[int] = typer.Option(
        None,
        "--target-sec",
        "-t",
        help="目標切段時長 (秒)，預設 600 秒 (10 分鐘)",
    ),
    work_dir: Optional[Path] = typer.Option(
        None,
        "--work-dir",
        "-w",
        help="自訂工作目錄 (預設為 work/<Course>/<Date>/)",
    ),
) -> None:
    """執行 Phase 1：檔名解析、音訊前處理開關濾波、與智慧 VAD 靜音切段。"""
    start_total_time = time.time()
    raw_path = Path(audio_file).resolve()

    if not raw_path.is_file():
        console.print(f"[bold red]❌ 找不到音檔：{raw_path}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold cyan]🎙️ 啟動 Phase 1 音訊前處理與切段管線：{raw_path.name}[/bold cyan]")

    # 1. Filename Parsing
    parsed = parse_filename(raw_path)
    if parsed.warning:
        console.print(f"[yellow]⚠️ {parsed.warning}[/yellow]")
    console.print(f"📌 課程名稱：[green]{parsed.course}[/green] | 課堂日期：[green]{parsed.date}[/green]")

    target_work_dir = Path(work_dir) if work_dir else parsed.work_dir
    target_work_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"📂 工作目錄：[dim]{target_work_dir}[/dim]\n")

    # 2. Configuration & Preprocess Options
    cfg = load_config()
    if pre is not None:
        pre_opts = PreprocessOptions.from_cli_flags(pre)
    else:
        pre_cfg = cfg.get("preprocessing", {})
        pre_opts = PreprocessOptions(**pre_cfg)

    seg_cfg = cfg.get("segmentation", {})
    if target_sec is not None:
        seg_cfg["target_chunk_sec"] = target_sec
    seg_opts = SegmentationOptions(**seg_cfg)

    # 3. Audio Preprocessing
    norm_audio_path = target_work_dir / "00_normalized.wav"
    console.print("[bold]⏳ 正在執行音訊前處理 (濾波鏈)...[/bold]")
    console.print(
        f"   - 高通濾波: [yellow]{pre_opts.hp}[/yellow] | "
        f"響度正規化: [yellow]{pre_opts.loud}[/yellow] | "
        f"動態平衡: [yellow]{pre_opts.dyn}[/yellow] | "
        f"頻譜降噪: [yellow]{pre_opts.nr}[/yellow]"
    )

    t0 = time.time()
    try:
        preprocess_audio(raw_path, norm_audio_path, opts=pre_opts)
    except Exception as e:
        console.print(f"[bold red]❌ 前處理失敗：{e}[/bold red]")
        raise typer.Exit(code=1)
    pre_duration = round(time.time() - t0, 2)
    console.print(f"[green]✔ 前處理完成！費時 {pre_duration} 秒[/green]")

    # 4. VAD Silence Segmentation
    chunks_dir = target_work_dir / "chunks"
    console.print(f"\n[bold]⏳ 正在進行 VAD 靜音切段 (目標長度: {seg_opts.target_chunk_sec}s)...[/bold]")
    t1 = time.time()
    try:
        chunks = segment_audio(norm_audio_path, chunks_dir, opts=seg_opts)
    except Exception as e:
        console.print(f"[bold red]❌ 切段失敗：{e}[/bold red]")
        raise typer.Exit(code=1)
    seg_duration = round(time.time() - t1, 2)
    console.print(f"[green]✔ 靜音切段完成！費時 {seg_duration} 秒，共產出 {len(chunks)} 個切片。[/green]\n")

    # 5. Save Run State (run.json)
    state = RunState(
        source_filename=raw_path.name,
        source_path=str(raw_path),
        course=parsed.course,
        date=parsed.date,
        pre_options=pre_opts,
        seg_options=seg_opts,
        chunks=chunks,
        stage_status={"preprocess": "DONE", "vad_segment": "DONE", "stt": "PENDING"},
        metadata={"total_elapsed_sec": round(time.time() - start_total_time, 2)},
    )
    run_file = save_run_state(state, target_work_dir)

    # 6. Summary Table Output
    table = Table(title="Phase 1 音訊切段清單 (Chunks Manifest)")
    table.add_column("片段", justify="center", style="cyan")
    table.add_column("檔名", style="bold")
    table.add_column("起始時間 (s)", justify="right")
    table.add_column("結束時間 (s)", justify="right")
    table.add_column("長度", justify="right", style="green")

    for c in chunks:
        mins = int(c.duration_sec // 60)
        secs = int(c.duration_sec % 60)
        dur_str = f"{mins:02d}:{secs:02d} ({c.duration_sec:.1f}s)"
        table.add_row(
            str(c.index),
            c.filename,
            f"{c.start_sec:.2f}",
            f"{c.end_sec:.2f}",
            dur_str,
        )

    console.print(table)
    console.print(f"\n[dim]狀態紀錄已儲存至：{run_file}[/dim]")
    console.print(f"[bold green]✨ Phase 1 流程執行成功！總耗時 {round(time.time() - start_total_time, 2)} 秒。[/bold green]\n")


@app.command("stt")
def stt_command(
    work_dir: Path = typer.Argument(..., help="已執行過 prep 的課堂工作目錄路徑 (包含 run.json 與 chunks/)"),
    channel: Optional[str] = typer.Option(
        None,
        "--channel",
        "-c",
        help="指定 STT 語音辨識通道 (C1: Gemini Flash, C2: Gemini 3.5 Transcribe)",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="覆寫 STT 模型 ID (例如 gemini-3.8-flash, gemini-3.5-flash)",
    ),
) -> None:
    """執行 Phase 2：調用 STT 辨識通道進行逐字稿轉錄，並產出 01_raw.txt。"""
    target_work_dir = Path(work_dir).resolve()
    state = load_run_state(target_work_dir)

    if not state:
        console.print(f"[bold red]❌ 找不到有效的 run.json，請先對音檔執行 'prep' 指令：{target_work_dir}[/bold red]")
        raise typer.Exit(code=1)

    if not state.chunks:
        console.print(f"[bold red]❌ 工作目錄中沒有音訊切片紀錄，請確認 'chunks/' 是否存在。[/bold red]")
        raise typer.Exit(code=1)

    cfg = load_config()
    stt_cfg = cfg.get("stt", {})
    selected_channel = (channel or stt_cfg.get("default_channel", "C1")).upper()

    console.print(f"\n[bold cyan]🎙️ 啟動 Phase 2 語音辨識 (STT)：{state.course} ({state.date})[/bold cyan]")
    console.print(f"📌 使用通道：[bold yellow]{selected_channel}[/bold yellow] | 總切片數：[green]{len(state.chunks)}[/green]")

    # Load course glossary if available
    glossary = load_course_glossary(state.course)
    if glossary:
        console.print(f"📚 已載入課程專業詞庫：[dim]{', '.join(glossary[:10])}... (共 {len(glossary)} 詞)[/dim]")
    else:
        console.print("[dim]ℹ️ 未載入課程專用詞庫 (可於 courses/{course}/glossary.txt 自訂)[/dim]")

    # Instantiate channel
    kwargs = {}
    if model:
        kwargs["primary_model"] = model
    try:
        stt_worker = get_stt_channel(selected_channel, **kwargs)
    except Exception as e:
        console.print(f"[bold red]❌ 初始化通道失敗: {e}[/bold red]")
        raise typer.Exit(code=1)

    # Transcribe chunks sequentially
    start_time = time.time()
    console.print(f"\n[bold]⏳ 正在進行語音轉錄 ({stt_worker.name})...[/bold]")

    full_transcription = stt_worker.transcribe_all(
        chunks=state.chunks,
        course=state.course,
        date=state.date,
        glossary=glossary,
    )

    # Save outputs
    output_files = save_stt_outputs(full_transcription, target_work_dir)

    # Update run state
    state.stage_status["stt"] = "DONE"
    state.metadata["stt_channel"] = selected_channel
    state.metadata["stt_total_chars"] = len(full_transcription.full_text)
    state.metadata["stt_latency_sec"] = full_transcription.total_latency_sec
    save_run_state(state, target_work_dir)

    total_time = round(time.time() - start_time, 2)
    console.print(f"[bold green]✔ 辨識完成！總耗時 {total_time} 秒，共辨識出 {len(full_transcription.full_text)} 個字。[/bold green]\n")

    # Output file paths
    console.print(f"📄 原始文字檔：[bold]{output_files['raw']}[/bold]")
    console.print(f"⏱️ 帶時間戳文字檔：[bold]{output_files['timestamped']}[/bold]")
    console.print(f"📊 結構化 JSON：[bold]{output_files['json']}[/bold]\n")

    # Print preview snippet
    preview = full_transcription.full_text[:300].replace("\n", " ")
    console.print(f"[dim]逐字稿預覽：\n\"{preview}...\"[/dim]\n")


@app.command("info")

def info_command(
    work_dir: Path = typer.Argument(..., help="工作目錄路徑 (包含 run.json)"),
) -> None:
    """檢視指定課堂工作目錄的執行狀態與切段資訊。"""
    from lecture_pipeline.state import load_run_state

    state = load_run_state(work_dir)
    if not state:
        console.print(f"[red]❌ 在 {work_dir} 找不到有效的 run.json[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]📋 課堂紀錄：{state.course} ({state.date})[/bold cyan]")
    console.print(f"Run ID: {state.run_id} | 建立時間: {state.created_at}")
    console.print(f"原始音檔: {state.source_filename}")
    console.print(f"階段狀態: {state.stage_status}")
    console.print(f"切段總數: {len(state.chunks)}")


@app.command("version")
def version_command() -> None:
    """顯示目前套件版本。"""
    from lecture_pipeline import __version__

    console.print(f"Lecture Pipeline v{__version__}")


if __name__ == "__main__":
    app()

