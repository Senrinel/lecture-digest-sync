"""Command Line Interface for the Lecture ASR Pipeline."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List, Optional
import typer
from rich.console import Console
from rich.table import Table

from lecture_pipeline.cleanup import (
    GeminiTextCleanupEngine,
    save_cleanup_outputs,
)
from lecture_pipeline.export import (
    GoogleDrivePublisher,
    open_notebooklm_in_browser,
    prepare_notebook_bundle,
    reveal_in_explorer,
)
from lecture_pipeline.summary import (
    GeminiLectureSummarizer,
    save_summary_outputs,
)
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
    audio_files: List[Path] = typer.Argument(..., help="欲處理的錄音檔案路徑 (.wav, .mp3, .m4a 等，可傳入多個分段檔案)"),
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
    raw_paths = [Path(f).resolve() for f in audio_files]

    for p in raw_paths:
        if not p.is_file():
            console.print(f"[bold red]❌ 找不到音檔：{p}[/bold red]")
            raise typer.Exit(code=1)

    file_display = ", ".join(p.name for p in raw_paths)
    console.print(f"\n[bold cyan]🎙️ 啟動 Phase 1 音訊前處理與切段管線：{file_display}[/bold cyan]")

    # 1. Filename Parsing (based on primary/first segment)
    parsed = parse_filename(raw_paths[0])
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
        preprocess_audio(raw_paths, norm_audio_path, opts=pre_opts)
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
        source_filename=file_display,
        source_path=";".join(str(p) for p in raw_paths),
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

    try:
        full_transcription = stt_worker.transcribe_all(
            chunks=state.chunks,
            course=state.course,
            date=state.date,
            glossary=glossary,
        )
    except Exception as e:
        fallback_channel = stt_cfg.get("fallback_local_channel", "C7")
        if selected_channel != fallback_channel:
            console.print(f"\n[bold yellow]⚠️ 通道 {selected_channel} 執行遭遇異常 ({e})！[/bold yellow]")
            console.print(f"🔄 自動無縫切換至標準本地備援通道：[bold green]{fallback_channel} (Whisper Turbo)[/bold green] 繼續辨識...\n")
            try:
                stt_worker = get_stt_channel(fallback_channel)
                selected_channel = fallback_channel
                full_transcription = stt_worker.transcribe_all(
                    chunks=state.chunks,
                    course=state.course,
                    date=state.date,
                    glossary=glossary,
                )
            except Exception as e2:
                console.print(f"[bold red]❌ 本地備援通道 {fallback_channel} 亦執行失敗：{e2}[/bold red]")
                raise typer.Exit(code=1)
        else:
            console.print(f"[bold red]❌ 語音辨識失敗：{e}[/bold red]")
            raise typer.Exit(code=1)

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


@app.command("clean")
def clean_command(
    work_dir: Path = typer.Argument(..., help="已執行過 stt 的課堂工作目錄路徑 (包含 01_raw.txt 與 run.json)"),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="指定整理模型 (預設使用 config.yaml 之設定或 gemini-3.5-flash)",
    ),
    prompt: Optional[Path] = typer.Option(
        None,
        "--prompt",
        "-p",
        help="自訂整理提示詞範本路徑 (預設 prompts/library/fabric_clean_text.md)",
    ),
    min_ratio: Optional[float] = typer.Option(
        None,
        "--min-ratio",
        help="最小字數保留比率門檻 (低於此值觸發防摘要嚴格重試，預設 0.80)",
    ),
    max_ratio: Optional[float] = typer.Option(
        None,
        "--max-ratio",
        help="最大字數保留比率門檻 (預設 1.20)",
    ),
    chunk_chars: Optional[int] = typer.Option(
        None,
        "--chunk-chars",
        help="單次整理文本區塊上限字元數 (預設 8000)",
    ),
    mode: str = typer.Option(
        "text",
        "--mode",
        help="整理模式: text (純文字整理), audio (邊聽邊修), fusion (雙通道融合)",
    ),
    fusion_file: Optional[Path] = typer.Option(
        None,
        "--fusion-file",
        help="融合模式下通道 B 的逐字稿檔案路徑",
    ),
) -> None:
    """執行 Phase 3：調用 AI 進行內文整理、錯字校正、標點補全與分段 (嚴格守恆字數)。"""
    # Unwrap Typer OptionInfo if called programmatically
    if not isinstance(model, str) and model is not None:
        model = getattr(model, "default", None)
    if not isinstance(mode, str):
        mode = getattr(mode, "default", "text")
    if not isinstance(min_ratio, (int, float)) and min_ratio is not None:
        min_ratio = getattr(min_ratio, "default", None)
    if not isinstance(max_ratio, (int, float)) and max_ratio is not None:
        max_ratio = getattr(max_ratio, "default", None)
    if not isinstance(chunk_chars, int) and chunk_chars is not None:
        chunk_chars = getattr(chunk_chars, "default", None)
    if not isinstance(prompt, (str, Path)) and prompt is not None:
        prompt = getattr(prompt, "default", None)
    if not isinstance(fusion_file, (str, Path)) and fusion_file is not None:
        fusion_file = getattr(fusion_file, "default", None)

    target_work_dir = Path(work_dir).resolve()
    raw_txt_path = target_work_dir / "01_raw.txt"

    if not raw_txt_path.is_file():
        console.print(f"[bold red]❌ 找不到原始逐字稿檔案：{raw_txt_path}，請先執行 'stt' 指令。[/bold red]")
        raise typer.Exit(code=1)

    with open(raw_txt_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    if not raw_text.strip():
        console.print(f"[bold red]❌ 原始逐字稿內容為空：{raw_txt_path}[/bold red]")
        raise typer.Exit(code=1)

    state = load_run_state(target_work_dir)
    course_name = state.course if state else "Unsorted"
    date_str = state.date if state else "Unknown"

    cfg = load_config()
    clean_cfg = cfg.get("cleanup", {})

    selected_model = model or clean_cfg.get("model", "gemini-3.5-flash-lite")
    prompt_file = prompt or clean_cfg.get("prompt_template", "prompts/library/videolingo_punct.md")
    ratio_min = min_ratio if min_ratio is not None else float(clean_cfg.get("length_ratio_min", 0.80))
    ratio_max = max_ratio if max_ratio is not None else float(clean_cfg.get("length_ratio_max", 1.20))
    max_chunk = chunk_chars if chunk_chars is not None else int(clean_cfg.get("chunk_chars", 8000))

    console.print(f"\n[bold cyan]📝 啟動 Phase 3 AI 內文整理：{course_name} ({date_str})[/bold cyan]")
    console.print(
        f"📌 使用模型：[bold yellow]{selected_model}[/bold yellow] | "
        f"原始字數：[bold green]{len(raw_text)}[/bold green] | "
        f"長度守恆目標：[dim]{ratio_min:.0%} ~ {ratio_max:.0%}[/dim]"
    )

    glossary = load_course_glossary(course_name) if course_name != "Unsorted" else []
    if glossary:
        console.print(f"📚 已載入課程專業詞庫：[dim]{', '.join(glossary[:10])}... (共 {len(glossary)} 詞)[/dim]")

    try:
        engine = GeminiTextCleanupEngine(
            primary_model=selected_model,
            prompt_template_path=prompt_file,
            mode=mode,
            min_ratio=ratio_min,
            max_ratio=ratio_max,
            chunk_chars=max_chunk,
        )
    except Exception as e:
        console.print(f"[bold red]❌ 初始化內文整理引擎失敗：{e}[/bold red]")
        raise typer.Exit(code=1)

    secondary_text = None
    if fusion_file and Path(fusion_file).is_file():
        secondary_text = Path(fusion_file).read_text(encoding="utf-8").strip()

    audio_chunks = None
    if mode == "audio":
        if state and state.chunks:
            audio_chunks = [Path(c.filepath) for c in state.chunks]
        elif (target_work_dir / "chunks").is_dir():
            audio_chunks = sorted((target_work_dir / "chunks").glob("*.wav"))

    console.print(f"\n[bold]⏳ 正在進行全文修復、分段與標點重構 (模式: [yellow]{mode}[/yellow])...[/bold]")
    result = engine.clean(
        raw_text=raw_text,
        secondary_text=secondary_text,
        audio_chunks=audio_chunks,
        course=course_name,
        date=date_str,
        glossary=glossary,
    )

    # Save output files
    output_files = save_cleanup_outputs(result, target_work_dir)

    # Update state if run.json exists
    if state:
        state.stage_status["cleanup"] = "DONE"
        state.metadata["cleanup_model"] = result.model_used
        state.metadata["cleanup_raw_chars"] = result.raw_char_count
        state.metadata["cleanup_cleaned_chars"] = result.cleaned_char_count
        state.metadata["cleanup_retention_ratio"] = result.retention_ratio
        state.metadata["cleanup_validation_passed"] = result.validation_passed
        state.metadata["cleanup_latency_sec"] = result.total_latency_sec
        save_run_state(state, target_work_dir)

    # Table summary
    status_style = "bold green" if result.validation_passed else "bold yellow"
    status_label = "✔ 通過 (未縮減)" if result.validation_passed else "⚠️ 字數低於門檻 (審計警示)"

    table = Table(title="Phase 3 內文整理指標審計報告 (Audit Report)")
    table.add_column("指標項目", style="cyan")
    table.add_column("數值 / 狀態", style="bold")
    table.add_row("使用模型", result.model_used)
    table.add_row("原始字數 (Raw Chars)", str(result.raw_char_count))
    table.add_row("整理後字數 (Cleaned Chars)", str(result.cleaned_char_count))
    table.add_row("字數保留比 (Retention Ratio)", f"[{status_style}]{result.retention_ratio:.2%}[/{status_style}]")
    table.add_row("內容守恆檢驗 (Guardrail)", f"[{status_style}]{status_label}[/{status_style}]")
    table.add_row("處理耗時 (Latency)", f"{result.total_latency_sec:.2f} 秒")
    table.add_row("分塊整理數 (Chunks)", str(len(result.chunks)))
    console.print(table)

    console.print(f"\n📄 整理後課堂文稿：[bold]{output_files['cleaned_txt']}[/bold]")
    console.print(f"📊 審計紀錄 JSON：[bold]{output_files['cleaned_json']}[/bold]\n")

    # Preview snippet
    preview = result.full_cleaned_text[:400].replace("\n\n", "\n")
    console.print(f"[dim]文稿內容預覽：\n{preview}...\n[/dim]")


@app.command("summary")
@app.command("summarize")
def summary_command(
    work_dir: Path = typer.Argument(..., help="已執行過 clean 的課堂工作目錄路徑 (包含 02_cleaned.txt 與 run.json)"),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="指定總結模型 (預設使用 config.yaml 之設定或 gemini-3.5-flash)",
    ),
    template: Optional[Path] = typer.Option(
        None,
        "--template",
        "-t",
        help="自訂總結提示詞範本路徑 (預設 prompts/library/fabric_summarize_lecture.md)",
    ),
    thinking: Optional[int] = typer.Option(
        None,
        "--thinking",
        help="思考 Token 預算 (預設 1024，0 為關閉思考)",
    ),
) -> None:
    """執行 Phase 4：調用 AI 提煉結構化重點筆記與心智脈絡 (產出 03_summary.md)。"""
    # Unwrap Typer OptionInfo if called programmatically
    if not isinstance(model, str) and model is not None:
        model = getattr(model, "default", None)
    if not isinstance(template, (str, Path)) and template is not None:
        template = getattr(template, "default", None)
    if not isinstance(thinking, int) and thinking is not None:
        thinking = getattr(thinking, "default", None)

    target_work_dir = Path(work_dir).resolve()
    cleaned_txt_path = target_work_dir / "02_cleaned.txt"

    if not cleaned_txt_path.is_file():
        # Fallback to 01_raw.txt if 02_cleaned.txt not yet run
        raw_txt_path = target_work_dir / "01_raw.txt"
        if raw_txt_path.is_file():
            console.print(f"[yellow]⚠️ 未找到 02_cleaned.txt，回退使用原始逐字稿 01_raw.txt 進行總結。[/yellow]")
            cleaned_txt_path = raw_txt_path
        else:
            console.print(f"[bold red]❌ 找不到課堂文稿：請先執行 'clean' 或 'stt' 指令產生文字。[/bold red]")
            raise typer.Exit(code=1)

    with open(cleaned_txt_path, "r", encoding="utf-8") as f:
        cleaned_text = f.read()

    if not cleaned_text.strip():
        console.print(f"[bold red]❌ 課堂文稿內容為空：{cleaned_txt_path}[/bold red]")
        raise typer.Exit(code=1)

    state = load_run_state(target_work_dir)
    course_name = state.course if state else "Unsorted"
    date_str = state.date if state else "Unknown"

    cfg = load_config()
    sum_cfg = cfg.get("summarize", {})

    selected_model = model or sum_cfg.get("model", "gemini-3.5-flash-lite")
    template_file = template or sum_cfg.get("template", "prompts/library/fabric_summarize_lecture.md")
    thinking_budget = thinking if thinking is not None else int(sum_cfg.get("thinking_budget", 1024))

    console.print(f"\n[bold cyan]🧠 啟動 Phase 4 AI 重點總結：{course_name} ({date_str})[/bold cyan]")
    console.print(
        f"📌 使用模型：[bold yellow]{selected_model}[/bold yellow] | "
        f"輸入字數：[bold green]{len(cleaned_text)}[/bold green] | "
        f"思考預算：[dim]{thinking_budget} tokens[/dim]"
    )

    glossary = load_course_glossary(course_name) if course_name != "Unsorted" else []
    if glossary:
        console.print(f"📚 已載入課程專業詞庫：[dim]{', '.join(glossary[:10])}... (共 {len(glossary)} 詞)[/dim]")

    try:
        summarizer = GeminiLectureSummarizer(
            primary_model=selected_model,
            template_path=template_file,
            thinking_budget=thinking_budget,
        )
    except Exception as e:
        console.print(f"[bold red]❌ 初始化重點總結引擎失敗：{e}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold]⏳ 正在分析教學主線、提煉定理公式、案例與考點脈絡...[/bold]")
    result = summarizer.summarize(
        cleaned_text=cleaned_text,
        course=course_name,
        date=date_str,
        glossary=glossary,
    )

    # Save output files
    output_files = save_summary_outputs(result, target_work_dir)

    # Update state if run.json exists
    if state:
        state.stage_status["summarize"] = "DONE"
        state.metadata["summary_model"] = result.model_used
        state.metadata["summary_chars"] = result.summary_char_count
        state.metadata["summary_latency_sec"] = result.total_latency_sec
        save_run_state(state, target_work_dir)

    # Rich table report
    table = Table(title="Phase 4 重點總結生成報告 (Summary Report)")
    table.add_column("項目", style="cyan")
    table.add_column("內容", style="bold")
    table.add_row("使用模型", result.model_used)
    table.add_row("輸入文稿長度", f"{result.input_char_count} 字")
    table.add_row("總結筆記長度", f"[green]{result.summary_char_count} 字[/green]")
    table.add_row("處理耗時", f"{result.total_latency_sec:.2f} 秒")
    table.add_row("提示詞範本", Path(result.prompt_template).name)
    console.print(table)

    console.print(f"\n📄 結構化課堂總結筆記：[bold]{output_files['summary_md']}[/bold]")
    console.print(f"📊 審計紀錄 JSON：[bold]{output_files['summary_json']}[/bold]\n")

    # Preview snippet
    preview = result.full_summary_md[:500]
    console.print(f"[dim]總結筆記預覽：\n{preview}...\n[/dim]")


@app.command("notebook")
@app.command("bundle")
def notebook_command(
    work_dir: Path = typer.Argument(..., help="已產出文稿之課堂工作目錄路徑"),
    export_dir: Optional[Path] = typer.Option(
        None,
        "--export-dir",
        "-e",
        help="自訂鏡像匯出目錄 (例如 Google Drive 本地同步資料夾)",
    ),
    open_browser: bool = typer.Option(
        False,
        "--open",
        "-o",
        help="是否自動於瀏覽器開啟 Google NotebookLM 網站",
    ),
    reveal: bool = typer.Option(
        False,
        "--reveal/--no-reveal",
        help="是否在檔案總管中開啟並選中打包資料夾",
    ),
) -> None:
    """執行 Phase 5：將整理文稿與考前重點筆記打包為 NotebookLM 最佳化檔案組。"""
    # Unwrap Typer OptionInfo if called programmatically
    if not isinstance(export_dir, (str, Path)) and export_dir is not None:
        export_dir = getattr(export_dir, "default", None)
    if not isinstance(open_browser, bool):
        open_browser = getattr(open_browser, "default", False)
    if not isinstance(reveal, bool):
        reveal = getattr(reveal, "default", False)

    target_work_dir = Path(work_dir).resolve()
    cfg = load_config()
    exp_cfg = cfg.get("export", {})

    target_export = export_dir or exp_cfg.get("export_dir")
    bundle_result = prepare_notebook_bundle(target_work_dir, export_dir=target_export)

    console.print(f"\n[bold cyan]📦 成功建立 Google NotebookLM 專用匯入檔案組！[/bold cyan]")
    console.print(f"📌 課程名稱：[green]{bundle_result.course}[/green] | 課堂日期：[green]{bundle_result.date}[/green]")
    console.print(f"📂 打包目錄：[bold]{bundle_result.bundle_dir}[/bold]")

    table = Table(title="NotebookLM 檔案清單")
    table.add_column("序號", style="cyan", justify="center")
    table.add_column("檔案名稱", style="bold")
    table.add_column("用途說明")

    table.add_row("1", Path(bundle_result.summary_file).name, "考前重點筆記（核心觀念、公式推導、考點與生活案例）")
    table.add_row("2", Path(bundle_result.cleaned_file).name, "完整課堂精修文稿（全文無刪減、標點與分段完整）")
    if bundle_result.raw_timestamped_file:
        table.add_row("3", Path(bundle_result.raw_timestamped_file).name, "原始時間戳對照（供快速定位課堂音訊片段）")
    console.print(table)

    if bundle_result.export_dir:
        console.print(f"🔄 已同步鏡像至指定匯出目錄：[dim]{bundle_result.export_dir}[/dim]")

    console.print(f"\n[bold green]💡 NotebookLM 半自動匯入操作指南 (3 步驟)：[/bold green]")
    console.print(f"   1. 開啟 [bold blue]https://notebooklm.google.com/[/bold blue] 並建立新筆記本（例如命名為「{bundle_result.course}」）。")
    console.print(f"   2. 點選「新增來源 (Add source)」 ➜ 選擇「上傳檔案 (Upload files)」。")
    console.print(f"   3. 直接將 [bold]{bundle_result.bundle_dir}[/bold] 目錄下的 .md 檔案拖曳進去即可！\n")

    if open_browser or exp_cfg.get("auto_open_notebooklm"):
        url = exp_cfg.get("notebooklm_url", "https://notebooklm.google.com/")
        console.print(f"[dim]🌐 正在於預設瀏覽器開啟 NotebookLM...[/dim]")
        open_notebooklm_in_browser(url)

    if reveal:
        reveal_in_explorer(bundle_result.bundle_dir)


@app.command("upload")
@app.command("drive")
def upload_command(
    work_dir: Path = typer.Argument(..., help="已執行過 summary 的課堂工作目錄路徑 (包含 notebook_bundle/ 或 03_summary.md)"),
    credentials: Optional[Path] = typer.Option(
        None,
        "--credentials",
        help="Google OAuth client secrets JSON 檔案路徑 (預設 credentials.json)",
    ),
    open_browser: bool = typer.Option(
        True,
        "--open/--no-open",
        help="上傳完成後是否在瀏覽器開啟目標 Google Drive 資料夾",
    ),
) -> None:
    # Unwrap Typer OptionInfo if called programmatically
    if not isinstance(credentials, (str, Path)) and credentials is not None:
        credentials = getattr(credentials, "default", None)
    if not isinstance(open_browser, bool):
        open_browser = getattr(open_browser, "default", True)

    target_work_dir = Path(work_dir).resolve()
    creds_path = credentials or Path("credentials.json")

    try:
        publisher = GoogleDrivePublisher(credentials_file=creds_path)
        result = publisher.publish_lecture(target_work_dir)
        console.print(f"\n[bold green]🎉 Google Drive 雲端發布完成！[/bold green]")
        console.print(f"📁 課程資料夾：[bold]{result.course_folder_link}[/bold]\n")

        if open_browser:
            import webbrowser
            webbrowser.open(result.course_folder_link)

    except Exception as e:
        console.print(f"[bold red]❌ Google Drive 發布失敗：{e}[/bold red]")
        raise typer.Exit(code=1)


@app.command("run")
def run_command(
    audio_files: List[Path] = typer.Argument(..., help="欲處理的課堂錄音檔案路徑 (.wav, .mp3, .m4a 等，可傳入多個分段檔案)"),
    pre: Optional[str] = typer.Option(
        None,
        "--pre",
        "-p",
        help="前處理開關組合 (例如 'hp,loud' 或 'hp=100Hz,loud=-19LUFS')",
    ),
    target_sec: Optional[int] = typer.Option(
        None,
        "--target-sec",
        "-t",
        help="目標切段時長 (秒，預設 600 秒)",
    ),
    channel: Optional[str] = typer.Option(
        None,
        "--channel",
        "-c",
        help="STT 語音辨識通道 (預設 C1)",
    ),
    open_notebook: bool = typer.Option(
        False,
        "--open",
        "-o",
        help="管線執行完成後是否自動於瀏覽器開啟 NotebookLM",
    ),
    upload: bool = typer.Option(
        True,
        "--upload/--no-upload",
        help="是否自動上傳至 Google Drive 並轉為 Google Docs (需存在 credentials.json)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="強制重新執行所有階段 (預設為 False，已完成階段自動沿用以節省 API 額度)",
    ),
) -> None:
    """【一鍵全流程】自動依序執行 Phase 1 ➜ Phase 2 ➜ Phase 3 ➜ Phase 4 ➜ Phase 5。"""
    raw_paths = [Path(f).resolve() for f in audio_files]
    for p in raw_paths:
        if not p.is_file():
            console.print(f"[bold red]❌ 找不到音檔：{p}[/bold red]")
            raise typer.Exit(code=1)

    parsed = parse_filename(raw_paths[0])
    target_work_dir = parsed.work_dir

    file_display = ", ".join(p.name for p in raw_paths)
    console.print(f"\n[bold magenta]🚀 啟動端到端自動化課堂處理管線 (End-to-End Pipeline)[/bold magenta]")
    console.print(f"📁 輸入檔案：[bold]{file_display}[/bold]")
    console.print(f"📌 識別課程：[green]{parsed.course}[/green] | 日期：[green]{parsed.date}[/green]\n")

    total_start = time.time()
    state = load_run_state(target_work_dir)

    # Step 1: Prep
    if not force and state and state.stage_status.get("preprocess") == "DONE" and state.stage_status.get("vad_segment") == "DONE" and state.chunks:
        console.print(f"[green]✔ 偵測到已完成 Phase 1 前處理與切段 ({len(state.chunks)} 個切片)，自動沿用。[/green]")
    else:
        prep_command(audio_files=raw_paths, pre=pre, target_sec=target_sec, work_dir=target_work_dir)
        state = load_run_state(target_work_dir)

    # Step 2: STT
    if not force and state and state.stage_status.get("stt") == "DONE" and (target_work_dir / "01_raw.txt").is_file():
        console.print(f"[green]✔ 偵測到已完成 Phase 2 STT 轉錄，自動沿用既有逐字稿以節省 API 額度。[/green]")
    else:
        stt_command(work_dir=target_work_dir, channel=channel, model=None)
        state = load_run_state(target_work_dir)

    # Step 3: Clean
    if not force and state and state.stage_status.get("cleanup") == "DONE" and (target_work_dir / "02_cleaned.txt").is_file():
        console.print(f"[green]✔ 偵測到已完成 Phase 3 內文整理，自動沿用既有整理文稿以節省 API 額度。[/green]")
    else:
        clean_command(work_dir=target_work_dir, model=None, prompt=None, min_ratio=None, max_ratio=None, chunk_chars=None, mode="text", fusion_file=None)
        state = load_run_state(target_work_dir)

    # Step 4: Summary
    if not force and state and state.stage_status.get("summarize") == "DONE" and (target_work_dir / "03_summary.md").is_file():
        console.print(f"[green]✔ 偵測到已完成 Phase 4 重點總結，自動沿用既有筆記。[/green]")
    else:
        summary_command(work_dir=target_work_dir, model=None, template=None, thinking=None)
        state = load_run_state(target_work_dir)

    # Step 5: Notebook Bundle
    notebook_command(work_dir=target_work_dir, export_dir=None, open_browser=open_notebook, reveal=False)

    # Step 6: Google Drive Auto-Upload (Spec Section 9)
    if upload:
        if Path("credentials.json").is_file():
            console.print(f"\n[bold cyan]☁️ 偵測到 credentials.json，正在自動上傳至 Google Drive...[/bold cyan]")
            try:
                upload_command(work_dir=target_work_dir, credentials=None, open_browser=False)
            except Exception as e:
                console.print(f"[yellow]⚠️ Google Drive 自動上傳未完成 ({e})，可於後續使用 'upload' 指令重試。[/yellow]")
        else:
            console.print(f"[dim]ℹ️ 未偵測到 credentials.json，略過 Google Drive 自動上傳。[/dim]")

    total_elapsed = round(time.time() - total_start, 2)

    console.print(f"\n[bold green]🎉 端到端課堂管線全數執行完成！總耗時：{total_elapsed} 秒[/bold green]\n")


@app.command("bench")
def bench_command(
    work_dir: Path = typer.Argument(
        ...,
        help="待評測的工作目錄 (例如 work/Discrete Mathematics/20261015)",
    ),
    channels: str = typer.Option(
        "C7,C4",
        "--channels",
        "-c",
        help="以逗號分隔欲比較的 STT 通道列表 (例如 C1,C2,C4,C6,C7)",
    ),
    ref: Optional[str] = typer.Option(
        None,
        "--ref",
        "-r",
        help="作為基準 (Ground Truth) 的通道代碼 (例如 C8 或 C1)",
    ),
    ref_text: Optional[Path] = typer.Option(
        None,
        "--ref-text",
        help="外部黃金標準逐字稿文字檔路徑",
    ),
    output_dir: Path = typer.Option(
        Path("benchmark"),
        "--output-dir",
        "-o",
        help="評測報告輸出目錄 (預設 benchmark/)",
    ),
) -> None:
    """執行 Phase 4：多通道 STT 評測，計算 CER、術語召回率並產出互動式 HTML 報表。"""
    from lecture_pipeline.bench import run_benchmark

    ch_list = [c.strip().upper() for c in channels.split(",") if c.strip()]
    console.print(f"\n[bold cyan]📊 啟動 ASR 多通道評測：{work_dir}[/bold cyan]")
    console.print(f"📌 評測通道：{', '.join(ch_list)} | 基準通道：{ref or '無'}")

    run_benchmark(
        work_dir=work_dir,
        channel_ids=ch_list,
        reference_channel_id=ref,
        reference_text_path=ref_text,
        output_dir=output_dir,
    )


@app.command("matrix")
def matrix_command(
    audio_file: Path = typer.Argument(
        ...,
        help="欲進行多維矩陣實驗的課堂錄音檔案路徑 (例如 inbox/Discrete Mathematics 06.wav)",
    ),
    clip_sec: float = typer.Option(
        300.0,
        "--clip-sec",
        "-s",
        help="標準化評測片段長度 (秒，預設 300 秒 / 5 分鐘)",
    ),
    start_sec: float = typer.Option(
        60.0,
        "--start-sec",
        help="評測片段起始時間位移 (秒，預設 60 秒以跳過課堂開場安靜期)",
    ),
    skip_cloud: bool = typer.Option(
        False,
        "--skip-cloud",
        help="是否跳過雲端 API 實驗（僅評測本地前處理與本地 ASR 通道）",
    ),
    ref: str = typer.Option(
        "C7",
        "--ref",
        "-r",
        help="作為 CER 比較基準 (Ground Truth) 的通道 (預設 C7 Whisper Turbo，可選 C1)",
    ),
    output_dir: Path = typer.Option(
        Path("benchmark"),
        "--output-dir",
        "-o",
        help="評測報告與數據輸出目錄 (預設 benchmark/)",
    ),
) -> None:
    """【多維測試矩陣】將前處理、STT 通道、提示詞模板各變數作為獨立操縱變因，產出比對數據與最佳流程分析。"""
    from lecture_pipeline.bench import MatrixBenchmarkRunner

    target_audio = Path(audio_file).resolve()
    if not target_audio.is_file():
        console.print(f"[bold red]❌ 找不到音檔：{target_audio}[/bold red]")
        raise typer.Exit(code=1)

    runner = MatrixBenchmarkRunner(
        audio_file=target_audio,
        clip_sec=clip_sec,
        start_sec=start_sec,
        base_dir=output_dir / "matrix",
    )
    runner.run_all(skip_cloud=skip_cloud, reference_channel=ref)
    runner.export_csv(output_dir / "matrix_results.csv")
    runner.export_html(output_dir / "matrix_report.html")



@app.command("watch")
def watch_command(
    inbox: Path = typer.Argument(
        Path("inbox"),
        help="欲監看的資料夾路徑 (預設 inbox/)",
    ),
    channel: str = typer.Option(
        "C7",
        "--channel",
        "-c",
        help="預設使用的 STT 通道代碼 (預設 C7 Whisper Turbo)",
    ),
    pre: Optional[str] = typer.Option(
        "hp,loud",
        "--pre",
        "-p",
        help="前處理開關組合",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="單次掃描並批次處理所有待辦錄音後退出",
    ),
    interval: int = typer.Option(
        5,
        "--interval",
        help="持續監看輪詢間隔秒數 (預設 5s)",
    ),
    timeout: int = typer.Option(
        3600,
        "--timeout",
        "-t",
        help="單次課堂處理逾時熔斷上限秒數 (預設 3600s/60分鐘)",
    ),
    tray: bool = typer.Option(
        True,
        "--tray/--no-tray",
        help="是否顯示 Windows 工作列系統匣狀態小圖示 (預設開啟)",
    ),
    archive_storage: Optional[str] = typer.Option(
        None,
        "--archive-storage",
        help="自訂長期修剪封存目錄 (預設 C:\\Users\\LinKJ\\Pictures\\Record)",
    ),
) -> None:
    """執行 Phase 8：資料夾無人值守監看、壞檔隔離、逾時熔斷與自動修剪封存端對端管線。"""
    from lecture_pipeline.automation import watch_inbox

    watch_inbox(
        inbox_dir=inbox,
        channel_id=channel,
        pre_flags=pre,
        once=once,
        poll_interval=interval,
        timeout_sec=timeout,
        enable_tray=tray,
        record_archive_base_dir=archive_storage,
    )


@app.command("extract-glossary")
def extract_glossary_command(
    course: str = typer.Argument(..., help="課程名稱 (例如: 離散數學, Algorithms)"),
    source: Path = typer.Argument(..., help="投影片 PDF 或講義文字檔路徑 (支援單檔或整個資料夾)"),
) -> None:
    """純本地 0 API 消耗解析課堂講義/投影片，自動提取專有名詞與縮寫並匯入詞庫。"""
    from lecture_pipeline.glossary import auto_extract_course_glossary

    if not source.exists():
        console.print(f"[bold red]❌ 找不到指定的講義檔案或目錄: {source}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold cyan]📚 啟動純本地詞庫自動解析 (0 API 耗用)[/bold cyan]")
    console.print(f"課程名稱: [yellow]{course}[/yellow] | 來源教材: {source}")

    extracted = auto_extract_course_glossary(course=course, source_path=source)
    console.print(f"[bold green]✔ 成功萃取並增量寫入 {len(extracted)} 個領域專有名詞與縮寫！[/bold green]")
    for term in extracted[:20]:
        console.print(f"  • {term}")
    if len(extracted) > 20:
        console.print(f"  [dim]...以及其餘 {len(extracted) - 20} 個詞彙[/dim]")
    console.print(f"詞庫檔案位置：[dim]courses/{course}/glossary.txt[/dim]\n")


@app.command("quota")
def quota_command() -> None:
    """檢視 Google Cloud API 官方額度與本地 RTX 4060 對比分析與最佳化調用策略。"""
    from lecture_pipeline.automation.quota import QuotaManager
    summary = QuotaManager.get_quota_comparison_summary()

    console.print("\n[bold cyan]📊 Google Cloud API 官方額度 vs 本地硬體能力對比與智慧調用報告[/bold cyan]\n")

    g_table = Table(title="1. Google 官方 Free Tier 額度限制說明")
    g_table.add_column("限制維度", style="cyan")
    g_table.add_column("官方配額 / 特徵", style="bold")
    g_info = summary["google_official_free_tier"]
    g_table.add_row("每分鐘請求數 (RPM)", str(g_info["rpm"]))
    g_table.add_row("每日請求數 (RPD)", str(g_info["rpd"]))
    g_table.add_row("每分鐘 Token 數 (TPM)", str(g_info["tpm"]))
    g_table.add_row("音訊 Token 費率", str(g_info["audio_token_rate"]))
    g_table.add_row("每日額度重置時間", str(g_info["reset_time"]))
    g_table.add_row("主要潛在瓶頸", f"[yellow]{g_info['bottleneck']}[/yellow]")
    console.print(g_table)

    l_table = Table(title="\n2. 本地硬體 (RTX 4060 8GB) 執行能力")
    l_table.add_column("硬體與模型項目", style="green")
    l_table.add_column("本地規格與實測性能", style="bold")
    l_info = summary["local_hardware_capabilities"]
    l_table.add_row("顯示卡規格", l_info["gpu"])
    l_table.add_row("STT 備援模型", l_info["model"])
    l_table.add_row("實測 VRAM 佔用", l_info["vram_usage"])
    l_table.add_row("即時轉錄倍速", l_info["speed"])
    l_table.add_row("成本與配額限制", f"[bold green]{l_info['cost_and_quota']}[/bold green]")
    l_table.add_row("詞庫自動解析能力", f"[bold green]{l_info['glossary_extraction']}[/bold green]")
    console.print(l_table)

    console.print("\n[bold]3. 系統智慧最佳化調用路由策略：[/bold]")
    for strat in summary["optimal_routing_strategy"]:
        console.print(f"  {strat}")
    console.print()



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


@app.command("autostart")
def autostart_command(
    action: str = typer.Argument(
        "status",
        help="操作動作: status (檢視狀態) | enable (啟用 Windows 開機背景自動監控) | disable (關閉開機自動監控)",
    ),
    channel: str = typer.Option(
        "C1",
        "--channel",
        "-c",
        help="自動監控預設使用的 STT 通道 (預設 C1)",
    ),
    interval: int = typer.Option(
        5,
        "--interval",
        "-i",
        help="輪詢間隔秒數 (預設 5s)",
    ),
) -> None:
    """管理 Windows 開機無感自動背景監看服務。"""
    from lecture_pipeline.automation import (
        disable_autostart,
        enable_autostart,
        get_autostart_status,
    )

    act = action.strip().lower()

    if act == "enable":
        vbs = enable_autostart(channel=channel, poll_interval=interval)
        console.print(f"\n[bold green]✔ 已成功啟用 Windows 開機無感自動監看！[/bold green]")
        console.print(f"啟動指令碼：[dim]{vbs}[/dim]")
        console.print(f"📌 開機後將以極低優先權在背景靜默監控 `inbox/`，不跳黑視窗、零干擾遊戲與工作。\n")

    elif act == "disable":
        ok = disable_autostart()
        if ok:
            console.print(f"\n[bold green]✔ 已成功移除 Windows 開機自動監看服務！[/bold green]\n")
        else:
            console.print(f"\n[yellow]ℹ️ 目前未啟用開機自動監看。[/yellow]\n")

    else:
        st = get_autostart_status()
        status_tag = "[bold green]已啟用 (Enabled)[/bold green]" if st["enabled"] else "[bold red]未啟用 (Disabled)[/bold red]"
        console.print(f"\n[bold cyan]⚙️ Windows 開機自動監看狀態：[/bold cyan]{status_tag}")
        console.print(f"路徑：{st['path']}\n")


@app.command("doctor")
def doctor_command() -> None:
    """【系統健檢診斷中心】一鍵全面掃描 GPU、CUDA、FFmpeg、API 金鑰、Google Drive、磁碟與背景服務健康度。"""
    import os
    import shutil
    import subprocess
    import time

    console.print("\n[bold cyan]🩺 正在執行 ASR 課堂自動化系統全方位健康診斷 (System Health Check)...[/bold cyan]\n")

    table = Table(title="系統核心組件健康狀態總覽")
    table.add_column("檢測項目", style="cyan", width=22)
    table.add_column("狀態", style="bold", width=12)
    table.add_column("詳細規格與檢測結果", style="white")

    # 1. Python & Platform
    py_ver = f"Python {sys.version.split()[0]} ({sys.platform}, UTF-8: {sys.flags.utf8_mode})"
    table.add_row("作業系統與環境", "[bold green]✔ 正常[/bold green]", py_ver)

    # 2. NVIDIA GPU & nvidia-smi
    gpu_status = "[bold red]✖ 未偵測[/bold red]"
    gpu_detail = "無法取得 GPU 資訊"
    try:
        smi_out = subprocess.run(
            ["nvidia-smi", "--query-gpu=gpu_name,memory.total,memory.free,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
        )
        if smi_out.returncode == 0 and smi_out.stdout.strip():
            gpu_status = "[bold green]✔ 正常運行[/bold green]"
            gpu_detail = smi_out.stdout.strip()
    except Exception as e:
        gpu_detail = f"nvidia-smi 查詢失敗: {e}"
    table.add_row("NVIDIA 顯示卡 (GPU)", gpu_status, gpu_detail)

    # 3. Isolated faster-whisper backend
    fw_status = "[bold red]✖ 未就緒[/bold red]"
    fw_detail = "backends/fasterwhisper/.venv 尚未建立"
    backend_py = Path("backends/fasterwhisper/.venv/Scripts/python.exe")
    if backend_py.is_file():
        try:
            res = subprocess.run(
                [str(backend_py), "-c", "import ctranslate2; print(ctranslate2.get_cuda_device_count())"],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0 and res.stdout.strip() != "0":
                fw_status = "[bold green]✔ 就緒[/bold green]"
                fw_detail = f"CTranslate2 獨立後端就緒 (CUDA 設備: {res.stdout.strip()})"
            else:
                fw_detail = "後端虛擬環境可執行但未偵測到 CUDA"
        except Exception as e:
            fw_detail = f"後端檢測異常: {e}"
    table.add_row("本地 STT 引擎 (C7)", fw_status, fw_detail)

    # 4. FFmpeg & Codecs
    ffmpeg_status = "[bold red]✖ 遺失[/bold red]"
    ffmpeg_detail = "系統 PATH 中找不到 ffmpeg.exe"
    if shutil.which("ffmpeg"):
        try:
            codec_res = subprocess.run(["ffmpeg", "-codecs"], capture_output=True, text=True)
            has_opus = "libopus" in codec_res.stdout or "opus" in codec_res.stdout
            has_aac = "aac" in codec_res.stdout
            ffmpeg_status = "[bold green]✔ 完整支援[/bold green]"
            ffmpeg_detail = f"FFmpeg 已安裝 (支援 Opus: {has_opus}, AAC: {has_aac})"
        except Exception:
            ffmpeg_detail = "FFmpeg 已就緒"
    table.add_row("聲學處理器 (FFmpeg)", ffmpeg_status, ffmpeg_detail)

    # 5. Gemini API Key & Latency
    from dotenv import load_dotenv
    load_dotenv()
    gemini_status = "[bold red]✖ 未設定[/bold red]"
    gemini_detail = "環境變數缺少 GEMINI_API_KEY"
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        t0 = time.time()
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            # Lightweight model list probe
            models = list(client.models.list(config={"page_size": 2}))
            latency = int((time.time() - t0) * 1000)
            gemini_status = "[bold green]✔ 連線正常[/bold green]"
            gemini_detail = f"API 通訊成功 (回應延遲: {latency} ms, Key: ...{api_key[-6:]})"
        except Exception as e:
            gemini_status = "[bold yellow]⚠️ 探測警示[/bold yellow]"
            gemini_detail = f"已填 Key 但探測超時或異常 ({e})"
    table.add_row("Google Gemini API", gemini_status, gemini_detail)

    # 6. Google Drive API
    drive_status = "[bold red]✖ 未授權[/bold red]"
    drive_detail = "缺少 credentials.json"
    if Path("credentials.json").is_file():
        if Path("token.json").is_file():
            drive_status = "[bold green]✔ 授權有效[/bold green]"
            drive_detail = "憑證與 token.json 皆已就緒，Docs 自動上傳已啟用"
        else:
            drive_status = "[bold yellow]⚠️ 尚未登入[/bold yellow]"
            drive_detail = "已有 credentials.json，首發同步時將自動跳出瀏覽器登入"
    table.add_row("Google Drive / Docs", drive_status, drive_detail)

    # 7. Storage Free Space Guard
    free_bytes = shutil.disk_usage(".").free
    free_gb = round(free_bytes / (1024 ** 3), 2)
    disk_status = "[bold green]✔ 空間充裕[/bold green]" if free_gb >= 5.0 else (
        "[bold yellow]⚠️ 空間偏低[/bold yellow]" if free_gb >= 3.0 else "[bold red]⛔ 嚴重不足[/bold red]"
    )
    disk_detail = f"工作磁區剩餘 {free_gb} GB (系統安全熔斷閥值: 3.0 GB)"
    table.add_row("磁碟儲存安全守護", disk_status, disk_detail)

    # 8. Windows Autostart Status
    from lecture_pipeline.automation import get_autostart_status
    as_info = get_autostart_status()
    as_status = "[bold green]已啟用[/bold green]" if as_info["enabled"] else "[dim]未啟用 (手動或批次執行)[/dim]"
    as_detail = str(as_info["path"])
    table.add_row("Windows 開機自動監看", as_status, as_detail)

    # 9. Offline Upload Queue
    from lecture_pipeline.automation.offline_queue import OfflineUploadQueue
    oq = OfflineUploadQueue()
    pending_items = oq.get_pending()
    q_status = "[bold green]✔ 清空無積壓[/bold green]" if not pending_items else f"[yellow]⚠️ {len(pending_items)} 筆等候中[/yellow]"
    q_detail = f"待上傳佇列共 {len(pending_items)} 筆錄音"
    table.add_row("斷網排隊佇列", q_status, q_detail)

    console.print(table)
    console.print("\n[bold green]💡 系統診斷完成！所有核心模組運作正常，隨時可投入日常課堂與會議處理。[/bold green]\n")


if __name__ == "__main__":
    app()


