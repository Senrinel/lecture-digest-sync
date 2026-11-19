"""Tests for multi-scenario prompt templates catalog, CRUD, REST API, and pipeline wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from lecture_pipeline import prompt_catalog
from lecture_pipeline.automation.watcher import run_pipeline_for_group
from lecture_pipeline.cleanup.base import CleanupResult
from lecture_pipeline.cleanup.text_cleanup import GeminiTextCleanupEngine
from lecture_pipeline.gui import bridge
from lecture_pipeline.gui.server import app
from lecture_pipeline.naming import parse_filename
from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt.base import FullTranscription
from lecture_pipeline.summary.base import SummaryResult
from lecture_pipeline.summary.summarizer import GeminiLectureSummarizer


def test_all_builtin_prompt_files_and_presets_valid():
    """Verify all 18 built-in prompt files and 10 scenario presets exist and load cleanly."""
    builtin_dir = prompt_catalog.get_builtin_prompts_dir()
    assert builtin_dir.is_dir()

    cleanup_ids = {p["id"] for p in prompt_catalog.BUILTIN_PROMPTS if p["stage"] == "cleanup"}
    summary_ids = {p["id"] for p in prompt_catalog.BUILTIN_PROMPTS if p["stage"] == "summary"}
    assert len(cleanup_ids) == 8
    assert len(summary_ids) == 10

    for item in prompt_catalog.BUILTIN_PROMPTS:
        tpl_path = builtin_dir / item["id"]
        assert tpl_path.is_file(), f"Missing built-in prompt file: {tpl_path}"
        text = tpl_path.read_text(encoding="utf-8")
        assert len(text.strip()) >= 200, f"Prompt template too short: {item['id']}"

    assert len(prompt_catalog.SCENARIO_PRESETS) == 10
    for preset in prompt_catalog.SCENARIO_PRESETS:
        assert preset["cleanup_template"] in cleanup_ids
        assert preset["summary_template"] in summary_ids


def test_prompt_catalog_crud_isolated(tmp_path: Path):
    """Verify creating, overriding, resetting, and deleting templates in an isolated dir."""
    catalog = prompt_catalog.list_prompt_templates(base_dir=tmp_path)
    assert len(catalog["cleanup_templates"]) == 8
    assert len(catalog["summary_templates"]) == 10
    assert len(catalog["presets"]) == 10

    # 1. Create custom template with path-traversal chars sanitized cleanly
    save_res = prompt_catalog.save_prompt_template(
        template_id="my_lab_meeting.md",
        stage="summary",
        name="實驗室進度組會自訂",
        category="meeting",
        description="每周研究組會專用",
        content="# 【研究主旨】\n- 實驗結果與下一步規劃。",
        base_dir=tmp_path,
    )
    assert save_res["status"] == "ok"
    assert save_res["is_builtin"] is False

    cat2 = prompt_catalog.list_prompt_templates(base_dir=tmp_path)
    custom_entry = next((t for t in cat2["summary_templates"] if t["id"] == "my_lab_meeting.md"), None)
    assert custom_entry is not None
    assert custom_entry["is_builtin"] is False
    assert custom_entry["name"] == "實驗室進度組會自訂"
    assert "<!-- META:" not in custom_entry["content"]
    assert "# 【研究主旨】" in custom_entry["content"]

    # 2. Override built-in template in isolated base_dir without modifying repo file
    builtin_orig = (prompt_catalog.get_builtin_prompts_dir() / "cornell_notes.md").read_text(encoding="utf-8")
    mod_res = prompt_catalog.save_prompt_template(
        template_id="cornell_notes.md",
        stage="summary",
        name="",
        category="",
        description="",
        content="# 自訂康乃爾筆記覆寫測試\n僅作為測試用。",
        base_dir=tmp_path,
    )
    assert mod_res["status"] == "ok"
    cat3 = prompt_catalog.list_prompt_templates(base_dir=tmp_path)
    cornell = next(t for t in cat3["summary_templates"] if t["id"] == "cornell_notes.md")
    assert cornell["is_modified"] is True
    assert "自訂康乃爾筆記覆寫測試" in cornell["content"]
    assert (prompt_catalog.get_builtin_prompts_dir() / "cornell_notes.md").read_text(encoding="utf-8") == builtin_orig

    # 3. Reset modified built-in template back to default
    rst_res = prompt_catalog.reset_prompt_template("cornell_notes.md", base_dir=tmp_path)
    assert rst_res["status"] == "ok"
    cat4 = prompt_catalog.list_prompt_templates(base_dir=tmp_path)
    cornell_restored = next(t for t in cat4["summary_templates"] if t["id"] == "cornell_notes.md")
    assert cornell_restored["is_modified"] is False
    assert cornell_restored["content"].strip() == builtin_orig.strip()

    # 4. Refuse deleting built-in template, allow deleting custom template
    assert prompt_catalog.delete_custom_prompt_template("cornell_notes.md", base_dir=tmp_path)["status"] == "error"
    assert prompt_catalog.delete_custom_prompt_template("my_lab_meeting.md", base_dir=tmp_path)["status"] == "ok"


def test_prompt_rest_api_endpoints_and_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify /api/prompts CRUD endpoints and UI controls render cleanly."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "paths": {"inbox_dir": str(tmp_path / "inbox")},
                "cleanup": {"mode": "text", "prompt_template": "videolingo_punct.md"},
                "summarize": {"template": "fabric_summarize_lecture.md", "thinking_budget": 1024},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "CONFIG_FILE_PATH", cfg_path)

    client = TestClient(app)

    # Check HTML includes prompt controls and modal
    res_html = client.get("/")
    assert res_html.status_code == 200
    for marker in ("studio-preset-select", "settings-prompts-card", "prompt-editor-modal", "loadPromptsFromServer"):
        assert marker in res_html.text

    # Initial GET /api/prompts
    r0 = client.get("/api/prompts")
    assert r0.status_code == 200
    d0 = r0.json()
    assert d0["active_preset"] == "course_general"
    assert d0["active_cleanup"] == "videolingo_punct.md"
    assert d0["active_summary"] == "fabric_summarize_lecture.md"
    assert len(d0["cleanup_templates"]) == 8
    assert len(d0["summary_templates"]) == 10
    assert len(d0["presets"]) == 10

    # Select scenario preset (STEM)
    r1 = client.post("/api/prompts/select", json={"preset_id": "course_stem"})
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["active_preset"] == "course_stem"
    assert d1["active_cleanup"] == "stem_formula_clean.md"
    assert d1["active_summary"] == "stem_problem_solving.md"

    # Mix custom pair -> active_preset becomes custom
    r2 = client.post("/api/prompts/select", json={"summary_template": "work_project_brief.md"})
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["active_cleanup"] == "stem_formula_clean.md"
    assert d2["active_summary"] == "work_project_brief.md"
    assert d2["active_preset"] == "custom"

    # Save custom template & auto-activate
    r3 = client.post(
        "/api/prompts/save",
        json={
            "template_id": "podcast_talk_v1",
            "stage": "summary",
            "name": "Podcast 訪談精華",
            "category": "talk",
            "description": "雙人對談亮點整理",
            "content": "# 【對談亮點】\n- 條列重點與引述。",
            "set_active": True,
        },
    )
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["active_summary"] == "podcast_talk_v1.md"

    # Deleting active custom template resets active_summary safely to default
    r4 = client.delete("/api/prompts/podcast_talk_v1.md")
    assert r4.status_code == 200
    d4 = r4.json()
    assert d4["active_summary"] == "fabric_summarize_lecture.md"

    # Edit and reset built-in
    r5 = client.post(
        "/api/prompts/save",
        json={
            "template_id": "meeting_summary.md",
            "stage": "summary",
            "content": "# 自訂會議\n- 測試內容。",
            "set_active": False,
        },
    )
    assert r5.status_code == 200
    assert any(t["id"] == "meeting_summary.md" and t["is_modified"] for t in r5.json()["summary_templates"])

    r6 = client.post("/api/prompts/reset", json={"template_id": "meeting_summary.md"})
    assert r6.status_code == 200
    assert not any(t["id"] == "meeting_summary.md" and t["is_modified"] for t in r6.json()["summary_templates"])


def test_engines_resolve_builtin_templates_outside_repo_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify cleanup and summarizer engines resolve template names even when CWD has no prompts/."""
    monkeypatch.chdir(tmp_path)

    engine = GeminiTextCleanupEngine(api_key="mock_key", prompt_template_path="stem_formula_clean.md")
    prompt_c = engine.build_prompt("這裡是微積分梯度推導逐字稿。")
    assert "LaTeX" in prompt_c

    summarizer = GeminiLectureSummarizer(api_key="mock_key", template_path="keynote_speech_digest.md")
    prompt_s = summarizer.build_prompt("這裡是趨勢演講文稿。", course="AI前沿演講")
    assert "講者核心主張與演講主旨" in prompt_s


@patch("lecture_pipeline.automation.watcher.prune_and_archive")
@patch("lecture_pipeline.automation.watcher.GeminiLectureSummarizer")
@patch("lecture_pipeline.automation.watcher.GeminiTextCleanupEngine")
@patch("lecture_pipeline.automation.watcher.get_stt_channel")
@patch("lecture_pipeline.automation.watcher.segment_audio")
@patch("lecture_pipeline.automation.watcher.preprocess_audio")
@patch("lecture_pipeline.automation.watcher.notify_processing_started")
def test_watcher_passes_configured_prompt_templates(
    mock_notify_start,
    mock_preprocess,
    mock_segment,
    mock_stt_channel,
    mock_cleaner_cls,
    mock_summarizer_cls,
    mock_prune,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify watcher reads selected cleanup and summary prompt templates from config.yaml."""
    cfg_path = tmp_path / "config.yaml"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    exports = tmp_path / "exports"
    work_base = tmp_path / "work"
    for d in (inbox, archive, exports, work_base):
        d.mkdir(parents=True, exist_ok=True)

    cfg_path.write_text(
        yaml.safe_dump(
            {
                "paths": {
                    "inbox_dir": str(inbox),
                    "archive_dir": str(archive),
                    "exports_dir": str(exports),
                    "work_dir": str(work_base),
                },
                "export": {"export_dir": str(exports)},
                "cleanup": {
                    "mode": "text",
                    "model": "gemini-3.5-flash-lite",
                    "prompt_template": "meeting_dialogue_clean.md",
                    "chunk_chars": 1500,
                    "overlap_chars": 200,
                    "length_ratio_min": 0.80,
                    "length_ratio_max": 1.20,
                },
                "summarize": {
                    "model": "gemini-3.5-flash-lite",
                    "template": "work_project_brief.md",
                    "thinking_budget": 1024,
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "CONFIG_FILE_PATH", cfg_path)

    audio_file = inbox / "產品週會_20261119.mp3"
    audio_file.write_bytes(b"fake_audio")

    def custom_parse(f):
        return parse_filename(f, base_work_dir=work_base)

    with patch("lecture_pipeline.automation.watcher.parse_filename", side_effect=custom_parse):
        mock_preprocess.side_effect = lambda src, dst, opts: dst.write_bytes(b"wav")
        mock_segment.return_value = [
            ChunkInfo(
                index=1,
                filename="chunk_001.wav",
                filepath=str(work_base / "chunk_001.wav"),
                start_sec=0.0,
                end_sec=30.0,
                duration_sec=30.0,
            )
        ]
        ch_mock = MagicMock()
        ch_mock.transcribe_all.return_value = FullTranscription(
            channel_name="C1",
            course="產品週會",
            date="20261119",
            chunks=[],
            full_text="本週專案進度如期完成百分之九十。",
            timestamped_text="[00:00] 本週專案進度如期完成百分之九十。",
            total_latency_sec=0.5,
        )
        mock_stt_channel.return_value = ch_mock

        cleaner_mock = MagicMock()
        cleaner_mock.clean.return_value = CleanupResult(
            full_cleaned_text="本週專案進度如期完成 90%。",
            raw_char_count=16,
            cleaned_char_count=16,
            retention_ratio=1.0,
            model_used="gemini-3.5-flash-lite",
            prompt_template="meeting_dialogue_clean.md",
            total_latency_sec=0.3,
        )
        mock_cleaner_cls.return_value = cleaner_mock

        summarizer_mock = MagicMock()
        summarizer_mock.summarize.return_value = SummaryResult(
            full_summary_md="# 【TL;DR 執行摘要】\n進度 90%。",
            input_char_count=16,
            summary_char_count=20,
            model_used="gemini-3.5-flash-lite",
            prompt_template="work_project_brief.md",
            total_latency_sec=0.4,
        )
        mock_summarizer_cls.return_value = summarizer_mock

        run_pipeline_for_group(
            audio_files=[audio_file],
            channel_id="C1",
            archive_dir=archive,
            upload_drive=False,
            export_dir=exports,
            record_archive_base_dir=tmp_path / "Record",
        )

    assert Path(mock_cleaner_cls.call_args.kwargs["prompt_template_path"]).name == "meeting_dialogue_clean.md"
    assert Path(mock_summarizer_cls.call_args.kwargs["template_path"]).name == "work_project_brief.md"
