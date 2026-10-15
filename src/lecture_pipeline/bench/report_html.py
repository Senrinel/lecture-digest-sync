"""Interactive HTML comparison report and CSV exporter for ASR benchmark results."""

from __future__ import annotations

import csv
import html
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ChannelBenchmarkResult:
    """Benchmark evaluation result for a single STT channel."""

    channel_id: str
    channel_name: str
    char_count: int
    duration_sec: float
    rtf: float
    cer: Optional[float] = None
    accuracy: Optional[float] = None
    term_recall: Optional[float] = None
    matched_terms: Optional[List[str]] = None
    missing_terms: Optional[List[str]] = None
    transcript_preview: str = ""
    full_transcript: str = ""


def generate_benchmark_html(
    course: str,
    date: str,
    results: List[ChannelBenchmarkResult],
    reference_channel: Optional[str] = None,
    output_html_path: Path | str = "benchmark/report.html",
) -> Path:
    """Generate an interactive HTML side-by-side comparison report."""
    out_path = Path(output_html_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Sort results by accuracy descending if available, else by duration
    sorted_results = sorted(
        results,
        key=lambda r: (r.accuracy is not None, r.accuracy or 0.0, -r.duration_sec),
        reverse=True,
    )

    table_rows = []
    for r in sorted_results:
        cer_display = f"{r.cer:.2%}" if r.cer is not None else "N/A (基準)"
        acc_display = f"{r.accuracy:.2%}" if r.accuracy is not None else "基準"
        recall_display = f"{r.term_recall:.2%}" if r.term_recall is not None else "N/A"
        is_ref = " (Reference)" if r.channel_id == reference_channel else ""

        table_rows.append(
            f"<tr>"
            f"<td><strong>{html.escape(r.channel_id)}</strong>{is_ref}</td>"
            f"<td>{html.escape(r.channel_name)}</td>"
            f"<td>{r.char_count:,}</td>"
            f"<td>{r.duration_sec:.2f}s</td>"
            f"<td>{r.rtf:.4f}</td>"
            f"<td><span class='badge {'green' if (r.cer or 0) < 0.15 else 'orange'}'>{cer_display}</span></td>"
            f"<td><span class='badge {'green' if (r.accuracy or 0) > 0.85 else 'blue'}'>{acc_display}</span></td>"
            f"<td>{recall_display}</td>"
            f"</tr>"
        )

    # Tabs for full transcript comparison
    tab_headers = []
    tab_contents = []
    for idx, r in enumerate(results):
        active_cls = "active" if idx == 0 else ""
        tab_headers.append(
            f"<button class='tab-btn {active_cls}' onclick='switchTab({idx})'>{html.escape(r.channel_id)}: {html.escape(r.channel_name)}</button>"
        )
        escaped_text = html.escape(r.full_transcript).replace("\n\n", "</p><p>").replace("\n", "<br>")
        tab_contents.append(
            f"<div id='tab-pane-{idx}' class='tab-pane {active_cls}'>"
            f"<h4>{html.escape(r.channel_name)} ({r.char_count:,} 字)</h4>"
            f"<div class='transcript-box'><p>{escaped_text}</p></div>"
            f"</div>"
        )

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ASR 多通道實測與評估報告 - {html.escape(course)} ({html.escape(date)})</title>
    <style>
        :root {{
            --bg: #f8fafc;
            --card-bg: #ffffff;
            --text: #0f172a;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --primary: #2563eb;
            --green: #10b981;
            --orange: #f59e0b;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        header {{
            background: var(--card-bg);
            padding: 24px;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            margin-bottom: 24px;
            border: 1px solid var(--border);
        }}
        h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
        .meta {{ color: var(--text-muted); font-size: 14px; }}
        .card {{
            background: var(--card-bg);
            padding: 24px;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            margin-bottom: 24px;
            border: 1px solid var(--border);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            margin-top: 12px;
        }}
        th, td {{
            padding: 12px;
            border-bottom: 1px solid var(--border);
            font-size: 14px;
        }}
        th {{ background: #f1f5f9; font-weight: 600; }}
        .badge {{
            padding: 4px 8px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 12px;
        }}
        .badge.green {{ background: #d1fae5; color: #065f46; }}
        .badge.orange {{ background: #fef3c7; color: #92400e; }}
        .badge.blue {{ background: #dbeafe; color: #1e40af; }}
        .tab-buttons {{
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
            flex-wrap: wrap;
        }}
        .tab-btn {{
            padding: 8px 16px;
            border: 1px solid var(--border);
            background: #f1f5f9;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 500;
        }}
        .tab-btn.active {{
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary);
        }}
        .tab-pane {{ display: none; }}
        .tab-pane.active {{ display: block; }}
        .transcript-box {{
            background: #f8fafc;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            max-height: 500px;
            overflow-y: auto;
            line-height: 1.7;
            font-size: 15px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🎙️ ASR 多通道評測對照報告</h1>
            <div class="meta">課程：<strong>{html.escape(course)}</strong> | 日期：<strong>{html.escape(date)}</strong> | 評測通道數：{len(results)}</div>
        </header>

        <div class="card">
            <h3>📊 客觀指標排行榜 (Objective Metrics Ranking)</h3>
            <table>
                <thead>
                    <tr>
                        <th>通道代碼</th>
                        <th>通道名稱</th>
                        <th>字數</th>
                        <th>耗時</th>
                        <th>RTF (速度比)</th>
                        <th>字錯誤率 (CER)</th>
                        <th>準確率 (1-CER)</th>
                        <th>術語召回率</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(table_rows)}
                </tbody>
            </table>
        </div>

        <div class="card">
            <h3>🔍 逐字稿全文並排/切換檢視 (Transcript Viewer)</h3>
            <div class="tab-buttons">
                {''.join(tab_headers)}
            </div>
            {''.join(tab_contents)}
        </div>
    </div>

    <script>
        function switchTab(targetIndex) {{
            const buttons = document.querySelectorAll('.tab-btn');
            const panes = document.querySelectorAll('.tab-pane');
            buttons.forEach((btn, idx) => {{
                btn.classList.toggle('active', idx === targetIndex);
            }});
            panes.forEach((pane, idx) => {{
                pane.classList.toggle('active', idx === targetIndex);
            }});
        }}
    </script>
</body>
</html>
"""
    out_path.write_text(html_content, encoding="utf-8")
    return out_path


def export_benchmark_csv(
    results: List[ChannelBenchmarkResult],
    output_csv_path: Path | str = "benchmark/results.csv",
) -> Path:
    """Export benchmark evaluation metrics to a CSV file."""
    out_path = Path(output_csv_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "channel_id",
        "channel_name",
        "char_count",
        "duration_sec",
        "rtf",
        "cer",
        "accuracy",
        "term_recall",
    ]

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "channel_id": r.channel_id,
                "channel_name": r.channel_name,
                "char_count": r.char_count,
                "duration_sec": r.duration_sec,
                "rtf": r.rtf,
                "cer": r.cer if r.cer is not None else "",
                "accuracy": r.accuracy if r.accuracy is not None else "",
                "term_recall": r.term_recall if r.term_recall is not None else "",
            })

    return out_path
