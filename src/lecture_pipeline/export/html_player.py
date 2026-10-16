"""Interactive HTML Audio-Synced Lecture Player generator.

Produces a standalone, zero-dependency HTML file allowing students to:
1. Listen to the high-compression audio with speed controls (1.0x - 2.0x).
2. Click any paragraph or timestamp to jump audio playback instantly.
3. Live highlight and auto-scroll to the currently active sentence as audio plays.
4. Search keywords across the full lecture transcript.
5. Review structured exam notes in an integrated tab.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import List


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{TITLE}} - 課堂同步播放與複習筆記</title>
    <style>
        :root {
            --primary: #2563eb;
            --primary-light: #dbeafe;
            --bg: #f8fafc;
            --card: #ffffff;
            --text: #1e293b;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --highlight: #fef08a;
            --active-bg: #eff6ff;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Microsoft JhengHei", sans-serif;
            margin: 0;
            padding: 0;
            background: var(--bg);
            color: var(--text);
            line-height: 1.7;
        }
        header {
            background: var(--card);
            border-bottom: 1px solid var(--border);
            padding: 16px 24px;
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        .header-content {
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }
        .title-block h1 {
            margin: 0;
            font-size: 1.25rem;
            color: var(--text);
        }
        .title-block span {
            font-size: 0.875rem;
            color: var(--text-muted);
        }
        .player-bar {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }
        audio {
            height: 40px;
            outline: none;
        }
        .speed-btn {
            background: var(--bg);
            border: 1px solid var(--border);
            padding: 6px 10px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85rem;
            font-weight: 500;
        }
        .speed-btn.active {
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }
        .container {
            max-width: 1200px;
            margin: 24px auto;
            padding: 0 16px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
        }
        @media (max-width: 900px) {
            .container { grid-template-columns: 1fr; }
        }
        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            height: calc(100vh - 180px);
            overflow-y: auto;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            position: sticky;
            top: -24px;
            background: var(--card);
            padding: 8px 0;
            border-bottom: 1px solid var(--border);
            z-index: 10;
        }
        .card-title {
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text);
        }
        .search-box {
            padding: 6px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            font-size: 0.875rem;
            width: 180px;
        }
        .transcript-p {
            padding: 10px 14px;
            margin-bottom: 8px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.15s ease;
            position: relative;
        }
        .transcript-p:hover {
            background: var(--bg);
        }
        .transcript-p.playing {
            background: var(--active-bg);
            border-left: 4px solid var(--primary);
        }
        .ts-tag {
            display: inline-block;
            background: var(--primary-light);
            color: var(--primary);
            font-size: 0.75rem;
            font-weight: 600;
            padding: 2px 6px;
            border-radius: 4px;
            margin-right: 8px;
            font-family: monospace;
            cursor: pointer;
        }
        .summary-body {
            white-space: pre-wrap;
            font-size: 0.95rem;
        }
        .search-highlight {
            background: var(--highlight);
            font-weight: bold;
        }
    </style>
</head>
<body>
    <header>
        <div class="header-content">
            <div class="title-block">
                <h1>{{TITLE}}</h1>
                <span>📅 錄音日期：{{DATE}} | 雙向同步音訊播放器</span>
            </div>
            <div class="player-bar">
                <audio id="audioPlayer" controls src="{{AUDIO_SRC}}"></audio>
                <button class="speed-btn" onclick="setSpeed(1.0)">1.0x</button>
                <button class="speed-btn" onclick="setSpeed(1.25)">1.25x</button>
                <button class="speed-btn active" onclick="setSpeed(1.5)">1.5x</button>
                <button class="speed-btn" onclick="setSpeed(1.75)">1.75x</button>
                <button class="speed-btn" onclick="setSpeed(2.0)">2.0x</button>
            </div>
        </div>
    </header>

    <div class="container">
        <!-- Left Column: Synchronized Cleaned Transcript -->
        <div class="card" id="transcriptCard">
            <div class="card-header">
                <span class="card-title">📖 課堂整理文稿 (點擊句子即時跳轉)</span>
                <input type="text" id="searchInput" class="search-box" placeholder="搜尋關鍵字..." oninput="searchTranscript()">
            </div>
            <div id="transcriptList">
                {{TRANSCRIPT_ITEMS}}
            </div>
        </div>

        <!-- Right Column: Structured Exam Notes -->
        <div class="card" id="summaryCard">
            <div class="card-header">
                <span class="card-title">📝 結構化重點總結筆記</span>
            </div>
            <div class="summary-body" id="summaryContent">
                {{SUMMARY_CONTENT}}
            </div>
        </div>
    </div>

    <script>
        const audio = document.getElementById('audioPlayer');
        const paragraphs = document.querySelectorAll('.transcript-p');

        function setSpeed(rate) {
            audio.playbackRate = rate;
            document.querySelectorAll('.speed-btn').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
        }

        function jumpToTime(seconds) {
            audio.currentTime = seconds;
            audio.play();
        }

        audio.addEventListener('timeupdate', () => {
            const curTime = audio.currentTime;
            let currentActive = null;

            paragraphs.forEach(p => {
                const start = parseFloat(p.getAttribute('data-start') || '0');
                const end = parseFloat(p.getAttribute('data-end') || '999999');
                if (curTime >= start && curTime < end) {
                    p.classList.add('playing');
                    currentActive = p;
                } else {
                    p.classList.remove('playing');
                }
            });

            if (currentActive && !window.userScrolling) {
                currentActive.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        });

        function searchTranscript() {
            const query = document.getElementById('searchInput').value.trim().toLowerCase();
            paragraphs.forEach(p => {
                const text = p.getAttribute('data-original-text') || p.innerText;
                if (!p.getAttribute('data-original-text')) {
                    p.setAttribute('data-original-text', text);
                }
                if (!query) {
                    p.style.display = '';
                    p.innerHTML = p.getAttribute('data-original-html');
                    return;
                }
                if (text.toLowerCase().includes(query)) {
                    p.style.display = '';
                    const regex = new RegExp(`(${query})`, 'gi');
                    p.innerHTML = p.getAttribute('data-original-html').replace(regex, '<span class="search-highlight">$1</span>');
                } else {
                    p.style.display = 'none';
                }
            });
        }

        // Cache initial HTML for search resets
        paragraphs.forEach(p => {
            p.setAttribute('data-original-html', p.innerHTML);
            p.setAttribute('data-original-text', p.innerText);
        });
    </script>
</body>
</html>
"""


def format_seconds(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS timestamp."""
    secs = int(seconds)
    hours = secs // 3600
    mins = (secs % 3600) // 60
    s = secs % 60
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{s:02d}"
    return f"{mins:02d}:{s:02d}"


def generate_interactive_player(
    work_dir: Path | str,
    course: str,
    date: str,
    audio_filename: str = "00_compressed.opus",
    output_filename: str = "04_player.html",
) -> Path:
    """Generate standalone audio-synced interactive lecture player HTML."""
    w_dir = Path(work_dir).resolve()
    out_path = w_dir / output_filename

    # Load cleaned text paragraphs
    cleaned_txt = w_dir / "02_cleaned.txt"
    cleaned_lines = []
    if cleaned_txt.is_file():
        raw_text = cleaned_txt.read_text(encoding="utf-8", errors="ignore")
        cleaned_lines = [line.strip() for line in raw_text.split("\n\n") if line.strip()]
    if not cleaned_lines:
        cleaned_lines = ["課堂文稿整理中..."]

    # Load chunk timestamps if state run.json exists
    run_json = w_dir / "run.json"
    chunk_intervals: List[tuple[float, float]] = []
    if run_json.is_file():
        try:
            with open(run_json, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            chunks = state_data.get("chunks", [])
            for c in chunks:
                start = float(c.get("start_sec", 0.0))
                end = float(c.get("end_sec", start + 600.0))
                chunk_intervals.append((start, end))
        except Exception:
            pass

    # Build paragraph items HTML
    transcript_html_parts = []
    total_paragraphs = len(cleaned_lines)
    for idx, text in enumerate(cleaned_lines):
        # Estimate timestamp interval if chunk info is sparse
        if chunk_intervals and idx < len(chunk_intervals):
            start_s, end_s = chunk_intervals[idx]
        elif chunk_intervals:
            last_end = chunk_intervals[-1][1]
            start_s = (last_end / total_paragraphs) * idx
            end_s = (last_end / total_paragraphs) * (idx + 1)
        else:
            start_s = idx * 60.0
            end_s = (idx + 1) * 60.0

        ts_str = format_seconds(start_s)
        safe_text = html.escape(text)

        p_html = (
            f'<div class="transcript-p" data-start="{start_s:.1f}" data-end="{end_s:.1f}" onclick="jumpToTime({start_s:.1f})">'
            f'<span class="ts-tag" title="點擊跳轉至此時間點">▶ {ts_str}</span>'
            f'<span>{safe_text}</span>'
            f'</div>'
        )
        transcript_html_parts.append(p_html)

    # Load summary notes
    summary_md = w_dir / "03_summary.md"
    summary_content = "課堂重點筆記生成中..."
    if summary_md.is_file():
        summary_content = summary_md.read_text(encoding="utf-8", errors="ignore")

    title = f"{course} ({date})"
    html_content = (
        HTML_TEMPLATE
        .replace("{{TITLE}}", html.escape(title))
        .replace("{{DATE}}", html.escape(date))
        .replace("{{AUDIO_SRC}}", html.escape(audio_filename))
        .replace("{{TRANSCRIPT_ITEMS}}", "\n".join(transcript_html_parts))
        .replace("{{SUMMARY_CONTENT}}", html.escape(summary_content))
    )

    out_path.write_text(html_content, encoding="utf-8")
    return out_path
