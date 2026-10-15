"""Interactive side-by-side HTML benchmark comparison report generator."""

import difflib
import json
from pathlib import Path
from typing import Dict, List, Optional
from lecture_pipeline.bench.cer import compute_cer, normalize_chinese_text_for_cer


def generate_diff_html(reference: str, hypothesis: str) -> str:
    """Generates an inline HTML diff with highlighted substitutions and insertions."""
    s = difflib.SequenceMatcher(None, reference, hypothesis)
    html_parts = []
    for tag, i1, i2, j1, j2 in s.get_opcodes():
        if tag == "equal":
            html_parts.append(hypothesis[j1:j2])
        elif tag == "replace":
            html_parts.append(f'<span class="diff-replace" title="原文: {reference[i1:i2]}">{hypothesis[j1:j2]}</span>')
        elif tag == "insert":
            html_parts.append(f'<span class="diff-insert">{hypothesis[j1:j2]}</span>')
        elif tag == "delete":
            html_parts.append(f'<span class="diff-delete" title="刪除">{reference[i1:i2]}</span>')
    return "".join(html_parts)


def build_comparison_report(
    gold_text: str,
    runs: List[Dict[str, str]],  # [{"name": "C1 (Gemini Flash)", "text": "...", "latency_sec": 1.2}, ...]
    output_html_path: str | Path = "benchmark/report.html",
    glossary: Optional[List[str]] = None,
) -> Path:
    """Builds a responsive side-by-side comparison HTML page."""
    outp = Path(output_html_path)
    outp.parent.mkdir(parents=True, exist_ok=True)

    metrics = []
    diff_sections = []

    for idx, run in enumerate(runs):
        name = run.get("name", f"Run {idx + 1}")
        hyp_text = run.get("text", "")
        latency = run.get("latency_sec", 0.0)

        # Compute CER
        res = compute_cer(gold_text, hyp_text, glossary=glossary)
        metrics.append({
            "name": name,
            "cer_percent": f"{res.cer * 100:.2f}%",
            "term_recall": f"{res.term_recall * 100:.1f}%",
            "latency": f"{latency:.2f}s",
            "sub": res.substitutions,
            "ins": res.insertions,
            "dele": res.deletions,
            "total_chars": len(hyp_text),
        })

        diff_content = generate_diff_html(gold_text, hyp_text)
        diff_sections.append(f"""
        <div class="model-column">
            <div class="model-header">
                <h3>{name}</h3>
                <div class="badges">
                    <span class="badge cer">CER: {res.cer * 100:.2f}%</span>
                    <span class="badge recall">詞彙召回: {res.term_recall * 100:.1f}%</span>
                    <span class="badge time">耗時: {latency:.2f}s</span>
                </div>
                <div class="rating-box">
                    <span>主觀評分：</span>
                    <select class="score-select" data-model="{name}">
                        <option value="">未評分</option>
                        <option value="5">⭐⭐⭐⭐⭐ (5分 極佳)</option>
                        <option value="4">⭐⭐⭐⭐ (4分 良好)</option>
                        <option value="3">⭐⭐⭐ (3分 可接受)</option>
                        <option value="2">⭐⭐ (2分 差)</option>
                        <option value="1">⭐ (1分 極差)</option>
                    </select>
                </div>
            </div>
            <div class="transcript-body">{diff_content}</div>
        </div>
        """)

    # Metrics table rows
    table_rows = "".join([
        f"<tr><td><b>{m['name']}</b></td><td>{m['cer_percent']}</td><td>{m['term_recall']}</td>"
        f"<td>{m['latency']}</td><td>{m['sub']}</td><td>{m['ins']}</td><td>{m['dele']}</td>"
        f"<td>{m['total_chars']}</td></tr>"
        for m in metrics
    ])

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <title>課堂錄音 ASR 成果比對報告</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Microsoft JhengHei", sans-serif; background: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
        h1, h2 {{ color: #0f172a; margin-top: 0; }}
        .card {{ background: #ffffff; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin-bottom: 24px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
        th, td {{ border: 1px solid #e2e8f0; padding: 10px 14px; text-align: left; }}
        th {{ background: #f1f5f9; font-weight: 600; }}
        .diff-container {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 20px; }}
        .model-column {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; }}
        .model-header {{ background: #f8fafc; padding: 16px; border-bottom: 1px solid #e2e8f0; }}
        .badges {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }}
        .badge {{ font-size: 12px; padding: 4px 8px; border-radius: 6px; font-weight: 600; }}
        .badge.cer {{ background: #fee2e2; color: #991b1b; }}
        .badge.recall {{ background: #dcfce7; color: #166534; }}
        .badge.time {{ background: #e0e7ff; color: #3730a3; }}
        .rating-box {{ margin-top: 10px; font-size: 13px; }}
        .score-select {{ padding: 4px 8px; border-radius: 6px; border: 1px solid #cbd5e1; }}
        .transcript-body {{ padding: 16px; line-height: 1.8; font-size: 15px; white-space: pre-wrap; height: 500px; overflow-y: auto; background: #fafafa; }}
        .gold-box {{ background: #f0fdf4; border: 1px solid #bbf7d0; padding: 16px; border-radius: 8px; margin-bottom: 20px; line-height: 1.8; }}
        .diff-replace {{ background: #fed7aa; color: #9a3412; text-decoration: underline; padding: 0 2px; border-radius: 2px; }}
        .diff-insert {{ background: #bbf7d0; color: #166534; font-weight: 600; padding: 0 2px; border-radius: 2px; }}
        .diff-delete {{ background: #fecaca; color: #991b1b; text-decoration: line-through; opacity: 0.6; padding: 0 2px; border-radius: 2px; }}
    </style>
</head>
<body>
    <h1>📊 課堂錄音語音辨識（ASR）多模型比對評測報告</h1>
    <div class="card">
        <h2>客觀評測指標彙整</h2>
        <table>
            <thead>
                <tr>
                    <th>通道名稱 / 模型</th>
                    <th>字錯誤率 (CER)</th>
                    <th>專有詞召回率</th>
                    <th>處理耗時</th>
                    <th>替代字數 (Sub)</th>
                    <th>插入字數 (Ins)</th>
                    <th>遺漏字數 (Del)</th>
                    <th>總字數</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h2>標準答案（人工校對基準稿）</h2>
        <div class="gold-box">{gold_text}</div>
    </div>

    <div class="card">
        <h2>多模型並排差異比對（含主觀評分）</h2>
        <div class="diff-container">
            {"".join(diff_sections)}
        </div>
    </div>

    <script>
        // Store subjective ratings locally
        document.querySelectorAll('.score-select').forEach(sel => {{
            const key = 'rating_' + sel.getAttribute('data-model');
            sel.value = localStorage.getItem(key) || '';
            sel.addEventListener('change', () => {{
                localStorage.setItem(key, sel.value);
            }});
        }});
    </script>
</body>
</html>
"""
    outp.write_text(html_content, encoding="utf-8")
    return outp
