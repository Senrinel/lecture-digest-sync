"""Unit tests for P4: Benchmarking and evaluation metrics (CER, Term Recall, Report HTML)."""

from pathlib import Path

import pytest
from lecture_pipeline.bench import (
    ChannelBenchmarkResult,
    compute_cer,
    compute_term_recall,
    export_benchmark_csv,
    generate_benchmark_html,
    normalize_text_for_cer,
)


def test_normalize_text_for_cer():
    """Verify punctuation removal, full-width conversion, and lowercasing."""
    raw = "這是一堂「離散數學」課程！Quick-Sort 演算法，對不對？（２０２６）"
    norm = normalize_text_for_cer(raw)
    assert "，" not in norm
    assert "「" not in norm
    assert "！" not in norm
    assert "？" not in norm
    assert "-" not in norm
    assert "quicksort" in norm
    assert "2026" in norm
    assert "離散數學" in norm


def test_compute_cer_identical():
    """Verify CER is 0.0 when texts are identical."""
    ref = "今天我們要學習圖論的最短路徑演算法。"
    hyp = "今天我們要學習圖論的最短路徑演算法。"
    res = compute_cer(ref, hyp)
    assert res.cer == 0.0
    assert res.accuracy == 1.0
    assert res.substitutions == 0
    assert res.deletions == 0
    assert res.insertions == 0


def test_compute_cer_with_errors():
    """Verify calculation of substitutions and insertions."""
    ref = "今天我們要學習快速排序演算法"
    hyp = "今天我們要研習快速排序法演算法"
    res = compute_cer(ref, hyp)
    assert res.cer > 0.0
    assert res.total_errors > 0


def test_compute_term_recall():
    """Verify term recall accurately identifies found and missing terms."""
    text = "這門課我們介紹了快速排序與二元搜尋樹，還有圖論。"
    terms = ["快速排序", "二元搜尋樹", "紅黑樹", "圖論"]
    res = compute_term_recall(text, terms)

    assert res.total_terms == 4
    assert res.matched_count == 3
    assert res.recall_rate == 0.75
    assert "紅黑樹" in res.missing_terms
    assert "快速排序" in res.matched_terms


def test_generate_benchmark_html_and_csv(tmp_path: Path):
    """Verify HTML report and CSV files are correctly generated."""
    r1 = ChannelBenchmarkResult(
        channel_id="C7",
        channel_name="Whisper Turbo",
        char_count=3000,
        duration_sec=19.5,
        rtf=0.0325,
        cer=0.05,
        accuracy=0.95,
        term_recall=0.90,
        full_transcript="完整文字內容測試",
    )
    r2 = ChannelBenchmarkResult(
        channel_id="C4",
        channel_name="Breeze ASR",
        char_count=3050,
        duration_sec=82.0,
        rtf=0.1367,
        cer=0.04,
        accuracy=0.96,
        term_recall=0.95,
        full_transcript="完整文字內容測試 2",
    )

    html_file = tmp_path / "report.html"
    csv_file = tmp_path / "results.csv"

    out_html = generate_benchmark_html(
        course="離散數學",
        date="20261015",
        results=[r1, r2],
        reference_channel="C8",
        output_html_path=html_file,
    )
    assert out_html.is_file()
    assert "離散數學" in out_html.read_text(encoding="utf-8")
    assert "Whisper Turbo" in out_html.read_text(encoding="utf-8")

    out_csv = export_benchmark_csv([r1, r2], csv_file)
    assert out_csv.is_file()
    content = out_csv.read_text(encoding="utf-8-sig")
    assert "C7" in content
    assert "C4" in content
