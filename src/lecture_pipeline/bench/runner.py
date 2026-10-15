"""Benchmark runner orchestrating multiple STT channels and computing comparative metrics."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional
from rich.console import Console
from rich.table import Table

from lecture_pipeline.bench.cer import compute_cer, compute_term_recall
from lecture_pipeline.bench.report_html import (
    ChannelBenchmarkResult,
    export_benchmark_csv,
    generate_benchmark_html,
)
from lecture_pipeline.glossary import load_course_glossary
from lecture_pipeline.state import ChunkInfo, RunState, load_run_state
from lecture_pipeline.stt import get_stt_channel

console = Console()


def run_benchmark(
    work_dir: Path | str,
    channel_ids: List[str],
    reference_channel_id: Optional[str] = None,
    reference_text_path: Optional[Path | str] = None,
    output_dir: Path | str = "benchmark",
) -> List[ChannelBenchmarkResult]:
    """Execute comparative benchmark across multiple channels on a workspace."""
    target_work_dir = Path(work_dir).resolve()
    state = load_run_state(target_work_dir)

    course = state.course if state else "Unsorted"
    date = state.date if state else "Unknown"
    chunks = state.chunks if state else []

    if not chunks:
        # Check if chunks directory exists
        chunk_files = sorted((target_work_dir / "chunks").glob("*.wav"))
        for idx, cf in enumerate(chunk_files, start=1):
            chunks.append(
                ChunkInfo(
                    index=idx,
                    filename=cf.name,
                    filepath=str(cf),
                    start_sec=0.0,
                    end_sec=0.0,
                    duration_sec=0.0,
                )
            )

    total_audio_sec = sum(c.duration_sec for c in chunks) if chunks else 1.0
    glossary = load_course_glossary(course) if course != "Unsorted" else []

    # 1. Resolve reference text if provided
    ref_text = ""
    if reference_text_path and Path(reference_text_path).is_file():
        ref_text = Path(reference_text_path).read_text(encoding="utf-8").strip()
    elif reference_channel_id:
        # Run or load reference channel first
        ref_ch = get_stt_channel(reference_channel_id)
        ref_trans = ref_ch.transcribe_all(chunks, course=course, date=date, glossary=glossary)
        ref_text = ref_trans.full_text

    benchmark_results: List[ChannelBenchmarkResult] = []

    for cid in channel_ids:
        cid_clean = cid.strip().upper()
        console.print(f"⏳ 正在評測通道 [bold yellow]{cid_clean}[/bold yellow]...")

        channel = get_stt_channel(cid_clean)
        t0 = time.time()
        try:
            trans = channel.transcribe_all(chunks, course=course, date=date, glossary=glossary)
            duration = trans.total_latency_sec or round(time.time() - t0, 2)
            full_text = trans.full_text
        except Exception as e:
            console.print(f"[red]❌ 通道 {cid_clean} 執行失敗: {e}[/red]")
            continue

        char_count = len(full_text)
        rtf = round(duration / max(1.0, total_audio_sec), 4)

        # CER calculation
        cer_val = None
        acc_val = None
        if ref_text and cid_clean != reference_channel_id:
            cer_res = compute_cer(reference_text=ref_text, hypothesis_text=full_text)
            cer_val = cer_res.cer
            acc_val = cer_res.accuracy

        # Term recall
        term_res = compute_term_recall(full_text, glossary) if glossary else None
        recall_val = term_res.recall_rate if term_res else None

        preview = full_text[:120] + "..." if len(full_text) > 120 else full_text

        benchmark_results.append(
            ChannelBenchmarkResult(
                channel_id=cid_clean,
                channel_name=channel.name,
                char_count=char_count,
                duration_sec=duration,
                rtf=rtf,
                cer=cer_val,
                accuracy=acc_val,
                term_recall=recall_val,
                matched_terms=term_res.matched_terms if term_res else [],
                missing_terms=term_res.missing_terms if term_res else [],
                transcript_preview=preview,
                full_transcript=full_text,
            )
        )

    # 2. Output HTML & CSV reports
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = export_benchmark_csv(benchmark_results, out_dir / "results.csv")
    html_path = generate_benchmark_html(
        course=course,
        date=date,
        results=benchmark_results,
        reference_channel=reference_channel_id,
        output_html_path=out_dir / "report.html",
    )

    # 3. Print Rich summary table
    table = Table(title=f"🏆 ASR 平行評測報告：{course} ({date})")
    table.add_column("通道", style="cyan", no_wrap=True)
    table.add_column("名稱", style="white")
    table.add_column("字數", justify="right", style="green")
    table.add_column("耗時", justify="right", style="yellow")
    table.add_column("RTF (速度比)", justify="right")
    table.add_column("字錯誤率 (CER)", justify="right")
    table.add_column("準確率", justify="right")
    table.add_column("術語召回", justify="right")

    for r in benchmark_results:
        cer_str = f"{r.cer:.2%}" if r.cer is not None else "基準"
        acc_str = f"{r.accuracy:.2%}" if r.accuracy is not None else "基準"
        rec_str = f"{r.term_recall:.2%}" if r.term_recall is not None else "-"
        table.add_row(
            r.channel_id,
            r.channel_name,
            f"{r.char_count:,}",
            f"{r.duration_sec:.2f}s",
            f"{r.rtf:.4f}",
            cer_str,
            acc_str,
            rec_str,
        )

    console.print(table)
    console.print(f"\n📊 評測 CSV 檔案：[bold]{csv_path}[/bold]")
    console.print(f"🌐 互動式 HTML 對照報表：[bold]{html_path}[/bold]\n")

    return benchmark_results
