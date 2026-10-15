from pathlib import Path
from lecture_pipeline.bench.report_html import build_comparison_report


def test_build_comparison_report(tmp_path):
    gold = "大家好今天我們討論機器學習的梯度下降演算法"
    runs = [
        {"name": "C1 (Gemini Flash)", "text": "大家好今天我們討論機器學習的梯度下降演算法", "latency_sec": 1.2},
        {"name": "C2 (Gemini Transcribe)", "text": "大家好今天我們討論機器學習的梯度下降演算法", "latency_sec": 0.8},
    ]
    out_html = tmp_path / "report.html"
    report_file = build_comparison_report(gold, runs, output_html_path=out_html)
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "課堂錄音 ASR 成果比對報告" in content
    assert "C1 (Gemini Flash)" in content
