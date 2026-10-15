"""Command Line Interface for the Lecture ASR Pipeline."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from lecture_pipeline.cleanup import (
    GeminiTextCleanupEngine,
    save_cleanup_outputs,
)
from lecture_pipeline.export import (
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
) -> None:
    """執行 Phase 3：調用 AI 進行內文整理、錯字校正、標點補全與分段 (嚴格守恆字數)。"""
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

    selected_model = model or clean_cfg.get("model", "gemini-3.5-flash")
    prompt_file = prompt or clean_cfg.get("prompt_template", "prompts/library/fabric_clean_text.md")
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
            min_ratio=ratio_min,
            max_ratio=ratio_max,
            chunk_chars=max_chunk,
        )
    except Exception as e:
        console.print(f"[bold red]❌ 初始化內文整理引擎失敗：{e}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold]⏳ 正在進行全文修復、分段與標點重構...[/bold]")
    result = engine.clean(
        raw_text=raw_text,
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

    selected_model = model or sum_cfg.get("model", "gemini-3.5-flash")
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


@app.command("run")
def run_command(
    audio_file: Path = typer.Argument(..., help="欲處理的課堂錄音檔案路徑 (.wav, .mp3, .m4a 等)"),
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
) -> None:
    """【一鍵全流程】自動依序執行 Phase 1 ➜ Phase 2 ➜ Phase 3 ➜ Phase 4 ➜ Phase 5。"""
    raw_path = Path(audio_file).resolve()
    if not raw_path.is_file():
        console.print(f"[bold red]❌ 找不到音檔：{raw_path}[/bold red]")
        raise typer.Exit(code=1)

    parsed = parse_filename(raw_path)
    target_work_dir = parsed.work_dir

    console.print(f"\n[bold magenta]🚀 啟動端到端自動化課堂處理管線 (End-to-End Pipeline)[/bold magenta]")
    console.print(f"📁 輸入檔案：[bold]{raw_path.name}[/bold]")
    console.print(f"📌 識別課程：[green]{parsed.course}[/green] | 日期：[green]{parsed.date}[/green]\n")

    total_start = time.time()

    # Step 1: Prep
    prep_command(audio_file=raw_path, pre=pre, target_sec=target_sec, work_dir=target_work_dir)

    # Step 2: STT
    stt_command(work_dir=target_work_dir, channel=channel, model=None)

    # Step 3: Clean
    clean_command(work_dir=target_work_dir, model=None, prompt=None, min_ratio=None, max_ratio=None, chunk_chars=None)

    # Step 4: Summary
    summary_command(work_dir=target_work_dir, model=None, template=None, thinking=None)

    # Step 5: Notebook Bundle
    notebook_command(work_dir=target_work_dir, export_dir=None, open_browser=open_notebook, reveal=False)

    total_elapsed = round(time.time() - total_start, 2)

    console.print(f"\n[bold green]🎉 端到端課堂管線全數執行完成！總耗時：{total_elapsed} 秒[/bold green]\n")


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

