"""Prompt template catalog, scenario presets, resolution, and CRUD helpers."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

CATEGORY_LABELS: Dict[str, str] = {
    "course": "大學課程",
    "meeting": "會議・工作",
    "speech": "演講・講座",
    "talk": "訪談・對談",
    "advanced": "進階校正模式",
    "custom": "自訂模板",
}

BUILTIN_PROMPTS: List[Dict[str, Any]] = [
    # ------------------------------------------------------------------
    # Phase 3: Cleanup templates (8)
    # ------------------------------------------------------------------
    {
        "id": "videolingo_punct.md",
        "stage": "cleanup",
        "name": "VideoLingo 高精度斷句與標點（預設）",
        "category": "course",
        "category_label": CATEGORY_LABELS["course"],
        "source": "VideoLingo (Apache 2.0)",
        "description": "100% 保留原始口語字句，專注於補全標點與合理斷句，最貼近逐字原意。",
    },
    {
        "id": "fabric_clean_text.md",
        "stage": "cleanup",
        "name": "Fabric 通用課堂精修與段落重整",
        "category": "course",
        "category_label": CATEGORY_LABELS["course"],
        "source": "danielmiessler/fabric clean_text (MIT)",
        "description": "剔除重複口頭禪與 STT 同音誤字並依主題分段，同時嚴格保留 80%~105% 原文內容。",
    },
    {
        "id": "stem_formula_clean.md",
        "stage": "cleanup",
        "name": "理工數學與程式課堂精修（LaTeX 公式校正）",
        "category": "course",
        "category_label": CATEGORY_LABELS["course"],
        "source": "STEM Lecture Template",
        "description": "將口語數學公式還原為 LaTeX，修正程式變數與演算法名稱大小寫，完整保留推導細節。",
    },
    {
        "id": "strict_verbatim.md",
        "stage": "cleanup",
        "name": "嚴格逐字速記校訂（僅補標點與分段）",
        "category": "talk",
        "category_label": CATEGORY_LABELS["talk"],
        "source": "Strict Verbatim Standard",
        "description": "連口語助詞都不刪減，適合需要逐字查證、法律紀錄或嚴格保存原話之場景。",
    },
    {
        "id": "meeting_dialogue_clean.md",
        "stage": "cleanup",
        "name": "會議・工作討論與多方訪談實錄整理",
        "category": "meeting",
        "category_label": CATEGORY_LABELS["meeting"],
        "source": "Meeting & Dialogue Clean Standard",
        "description": "標記提問、進度回報與決議轉換脈絡，精準保留人名、金額、數據與截止日期原話。",
    },
    {
        "id": "speech_prose_clean.md",
        "stage": "cleanup",
        "name": "演講・論壇與深度談話流暢實錄",
        "category": "speech",
        "category_label": CATEGORY_LABELS["speech"],
        "source": "Speech Prose Clean Standard",
        "description": "在不刪減細節與字數守恆前提下順暢斷句，保留講者敘事節奏、案例與核心金句。",
    },
    {
        "id": "audio_grounded_cleanup.md",
        "stage": "cleanup",
        "name": "多模態聽音修正草稿（Audio 模式用）",
        "category": "advanced",
        "category_label": CATEGORY_LABELS["advanced"],
        "source": "Audio-Grounded Cleanup",
        "description": "搭配 Gemini 多模態邊聽原音邊修正同音字與專有名詞（cleanup.mode 為 audio 時使用）。",
    },
    {
        "id": "fusion_cleanup.md",
        "stage": "cleanup",
        "name": "雙通道逐字稿交叉融合（Fusion 模式用）",
        "category": "advanced",
        "category_label": CATEGORY_LABELS["advanced"],
        "source": "Dual-Channel Fusion",
        "description": "交叉比對通道 A 與通道 B 逐字稿消除漏聽或幻覺（cleanup.mode 為 fusion 時使用）。",
    },
    # ------------------------------------------------------------------
    # Phase 4: Summary templates (10)
    # ------------------------------------------------------------------
    {
        "id": "fabric_summarize_lecture.md",
        "stage": "summary",
        "name": "Fabric 大學課堂重點筆記與考點總結（預設）",
        "category": "course",
        "category_label": CATEGORY_LABELS["course"],
        "source": "danielmiessler/fabric summarize_lecture (MIT)",
        "description": "涵蓋課程主旨、章節大綱、核心定義、步驟推導、課堂案例與考前高頻提醒。",
    },
    {
        "id": "stem_problem_solving.md",
        "stage": "summary",
        "name": "理工電資・定理推導與解題 SOP 筆記",
        "category": "course",
        "category_label": CATEGORY_LABELS["course"],
        "source": "STEM Problem-Solving Architecture",
        "description": "聚焦物理/數學直覺、LaTeX 公式、推導步驟、例題實作拆解與邊界計算陷阱。",
    },
    {
        "id": "cornell_notes.md",
        "stage": "summary",
        "name": "康乃爾筆記法 (Cornell Notes 5R 結構)",
        "category": "course",
        "category_label": CATEGORY_LABELS["course"],
        "source": "Cornell Note-Taking System",
        "description": "經典「線索提問欄 + 重點筆記欄 + 摘要回顧欄」，最適合課後自我檢核複習。",
    },
    {
        "id": "exam_quiz_prep.md",
        "stage": "summary",
        "name": "考前衝刺重點速查與模擬題庫（Active Recall）",
        "category": "course",
        "category_label": CATEGORY_LABELS["course"],
        "source": "danielmiessler/fabric create_quiz + Active Recall",
        "description": "提供一頁式速查表、易混淆對比表，以及依課程實況設計之模擬測驗題與詳解。",
    },
    {
        "id": "humanities_seminar.md",
        "stage": "summary",
        "name": "人文社科・文獻導讀與思辨論證筆記",
        "category": "course",
        "category_label": CATEGORY_LABELS["course"],
        "source": "danielmiessler/fabric analyze_paper + Seminar",
        "description": "梳理學術背景、理論學派主張、前提至結論的論證架構與研討批判問題。",
    },
    {
        "id": "meeting_summary.md",
        "stage": "summary",
        "name": "會議紀錄・決議共識與待辦追蹤清單",
        "category": "meeting",
        "category_label": CATEGORY_LABELS["meeting"],
        "source": "danielmiessler/fabric summarize_meeting Style",
        "description": "產出會議主旨、討論議題進度、拍板決議事項、負責人 Action Items 表格與風險追蹤。",
    },
    {
        "id": "work_project_brief.md",
        "stage": "summary",
        "name": "工作匯報・專案進度與管理決策簡報",
        "category": "meeting",
        "category_label": CATEGORY_LABELS["meeting"],
        "source": "Executive Brief / Pyramid Principle",
        "description": "採用結論先行 TL;DR、KPI/OKR 指標達成狀態、瓶頸根因、拍板方案與時程表。",
    },
    {
        "id": "keynote_speech_digest.md",
        "stage": "summary",
        "name": "專題演講・趨勢論壇與技術分享精華",
        "category": "speech",
        "category_label": CATEGORY_LABELS["speech"],
        "source": "Conference & Keynote Digest",
        "description": "提煉講者核心主張、演講脈絡、趨勢數據、代表性故事金句與立即可行的啟發。",
    },
    {
        "id": "extract_wisdom.md",
        "stage": "summary",
        "name": "Fabric 核心洞見・思維金句與知識提煉",
        "category": "speech",
        "category_label": CATEGORY_LABELS["speech"],
        "source": "danielmiessler/fabric extract_wisdom (MIT)",
        "description": "高濃度萃取核心洞見、關鍵概念、講師語錄與跨場景通用的行動建議。",
    },
    {
        "id": "dialogue_interview_notes.md",
        "stage": "summary",
        "name": "深度訪談・對談沙龍與焦點座談整理",
        "category": "talk",
        "category_label": CATEGORY_LABELS["talk"],
        "source": "danielmiessler/fabric summarize_debate + Q&A Synthesis",
        "description": "依討論主題串聯完整問答脈絡，呈現各方立場共識分歧對照表與原話引述。",
    },
]

_BUILTIN_MAP: Dict[str, Dict[str, Any]] = {item["id"]: item for item in BUILTIN_PROMPTS}

SCENARIO_PRESETS: List[Dict[str, str]] = [
    {
        "id": "course_general",
        "name": "🎓 大學通用課程（預設）",
        "category": "course",
        "cleanup_template": "videolingo_punct.md",
        "summary_template": "fabric_summarize_lecture.md",
        "description": "逐字高精度斷句搭配全方位結構化課程筆記，適合一般通識與專業課程。",
    },
    {
        "id": "course_stem",
        "name": "📐 理工電資與數學推導",
        "category": "course",
        "cleanup_template": "stem_formula_clean.md",
        "summary_template": "stem_problem_solving.md",
        "description": "自動校正 LaTeX 數學式與程式碼大小寫，產出定理公式推導與例題解題步驟。",
    },
    {
        "id": "course_cornell",
        "name": "📝 康乃爾筆記與課後複習",
        "category": "course",
        "cleanup_template": "fabric_clean_text.md",
        "summary_template": "cornell_notes.md",
        "description": "文稿去贅詞分段後，產出包含自測問題線索欄與摘要欄的康乃爾筆記。",
    },
    {
        "id": "course_exam",
        "name": "🎯 考前衝刺與模擬題庫",
        "category": "course",
        "cleanup_template": "fabric_clean_text.md",
        "summary_template": "exam_quiz_prep.md",
        "description": "提煉一頁式考點速查與易混淆概念對比，並附依實況設計之模擬考題與詳解。",
    },
    {
        "id": "course_humanities",
        "name": "📚 人文社科與文獻思辨",
        "category": "course",
        "cleanup_template": "fabric_clean_text.md",
        "summary_template": "humanities_seminar.md",
        "description": "聚焦理論流派、學者論證結構與正反交鋒思考題，適合 Seminar 與人文課堂。",
    },
    {
        "id": "meeting_standard",
        "name": "💼 會議紀錄與待辦追蹤",
        "category": "meeting",
        "cleanup_template": "meeting_dialogue_clean.md",
        "summary_template": "meeting_summary.md",
        "description": "保留會議數字承諾與發言轉換，產出決議事項與 Action Items 責任追蹤表。",
    },
    {
        "id": "work_brief",
        "name": "📊 工作匯報與專案決策",
        "category": "meeting",
        "cleanup_template": "meeting_dialogue_clean.md",
        "summary_template": "work_project_brief.md",
        "description": "結論先行 Executive Summary，涵蓋 OKR/KPI 指標、瓶頸診斷與專案行動時程。",
    },
    {
        "id": "speech_keynote",
        "name": "🎤 專題演講與論壇講座",
        "category": "speech",
        "cleanup_template": "speech_prose_clean.md",
        "summary_template": "keynote_speech_digest.md",
        "description": "整理為流暢演講實錄，並提煉講者核心主張、趨勢數據、金句與實踐啟發。",
    },
    {
        "id": "speech_wisdom",
        "name": "💡 深度洞見與金句提煉",
        "category": "speech",
        "cleanup_template": "speech_prose_clean.md",
        "summary_template": "extract_wisdom.md",
        "description": "採用 Fabric Extract Wisdom 框架，高密度提取關鍵概念、經典語錄與行動建議。",
    },
    {
        "id": "talk_interview",
        "name": "🎙️ 訪談對談與沙龍座談",
        "category": "talk",
        "cleanup_template": "meeting_dialogue_clean.md",
        "summary_template": "dialogue_interview_notes.md",
        "description": "梳理雙人/多人對談問答脈絡，生成各方觀點立場對照表與精彩原話引述。",
    },
]

_META_COMMENT_RE = re.compile(r"^\s*<!--\s*META:\s*(\{.*?\})\s*-->\s*\n?", re.DOTALL)


def _sanitize_template_id(raw_id: str) -> str:
    """Validate and normalize a prompt template filename to prevent path traversal."""
    cleaned = Path((raw_id or "").strip()).name
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cleaned).strip(" .")
    if not cleaned:
        raise ValueError("提示詞模板識別名稱不可為空")
    if not cleaned.lower().endswith(".md"):
        cleaned = f"{cleaned}.md"
    if cleaned in (".md", "..md") or ".." in cleaned:
        raise ValueError("提示詞模板識別名稱不合法")
    return cleaned


def get_builtin_prompts_dir() -> Path:
    """Locate shipped read-only factory default prompt library directory."""
    candidates: List[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "prompts" / "library")
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "_internal" / "prompts" / "library")
        candidates.append(exe_dir / "prompts" / "library")

    # Source repo layout: src/lecture_pipeline/prompt_catalog.py -> parents[2] == repo root
    repo_lib = Path(__file__).resolve().parents[2] / "prompts" / "library"
    candidates.append(repo_lib)
    candidates.append(Path("prompts/library").resolve())

    for cand in candidates:
        if cand.is_dir():
            return cand
    return repo_lib


def get_active_prompts_dir(base_dir: Optional[Path] = None) -> Path:
    """Return writable prompt library directory scoped to base_dir when supplied."""
    if base_dir is not None:
        return Path(base_dir).resolve() / "prompts" / "library"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "prompts" / "library"
    return Path("prompts/library").resolve()


def _parse_custom_meta(raw_text: str) -> tuple[Dict[str, str], str]:
    """Extract optional <!-- META: {...} --> header and return (meta, body)."""
    m = _META_COMMENT_RE.match(raw_text or "")
    if not m:
        return {}, raw_text
    try:
        parsed = json.loads(m.group(1))
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}, raw_text[m.end():]
    except Exception:
        pass
    return {}, raw_text


def _strip_meta_if_any(raw_text: str) -> str:
    _, body = _parse_custom_meta(raw_text)
    return body


def resolve_prompt_file(
    template_name_or_path: str | Path,
    base_dir: Optional[Path] = None,
) -> Path:
    """Resolve a prompt template filename or path across active overrides, CWD, and built-ins."""
    raw = str(template_name_or_path).strip()
    p = Path(raw)
    if p.is_absolute() and p.is_file():
        return p

    tpl_name = p.name if ("/" in raw or "\\" in raw) else raw
    active_dir = get_active_prompts_dir(base_dir)
    active_cand = active_dir / tpl_name
    if active_cand.is_file():
        return active_cand

    if p.is_file():
        return p

    builtin_dir = get_builtin_prompts_dir()
    for cand in (
        Path("prompts/library") / tpl_name,
        Path("prompts") / tpl_name,
        builtin_dir / tpl_name,
    ):
        if cand.is_file():
            return cand

    return active_cand


def _read_builtin_default(template_id: str, active_dir: Path) -> Optional[str]:
    """Read pristine built-in template text for preview or factory reset."""
    factory_bak = active_dir / ".factory_defaults" / template_id
    if factory_bak.is_file():
        return factory_bak.read_text(encoding="utf-8")

    builtin_dir = get_builtin_prompts_dir()
    builtin_file = builtin_dir / template_id
    if builtin_file.is_file():
        return builtin_file.read_text(encoding="utf-8")
    return None


def list_prompt_templates(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """List all built-in and custom prompt templates grouped by stage, plus scenario presets."""
    builtin_dir = get_builtin_prompts_dir()
    active_dir = get_active_prompts_dir(base_dir)
    factory_dir = active_dir / ".factory_defaults"

    cleanup_templates: List[Dict[str, Any]] = []
    summary_templates: List[Dict[str, Any]] = []

    seen_ids: set[str] = set()
    for meta in BUILTIN_PROMPTS:
        tid = meta["id"]
        seen_ids.add(tid)
        active_file = active_dir / tid
        builtin_file = builtin_dir / tid
        factory_file = factory_dir / tid

        content = ""
        if active_file.is_file():
            content = active_file.read_text(encoding="utf-8")
        elif builtin_file.is_file():
            content = builtin_file.read_text(encoding="utf-8")

        default_content = ""
        if factory_file.is_file():
            default_content = factory_file.read_text(encoding="utf-8")
        elif builtin_file.is_file():
            default_content = builtin_file.read_text(encoding="utf-8")

        is_modified = bool(
            content and default_content and content.strip() != default_content.strip()
        )
        entry = {
            **meta,
            "is_builtin": True,
            "is_modified": is_modified,
            "content": content,
        }
        if meta["stage"] == "cleanup":
            cleanup_templates.append(entry)
        else:
            summary_templates.append(entry)

    # Scan user-created custom .md templates in active_dir (or builtin_dir if local)
    scan_dirs = [active_dir]
    if builtin_dir.resolve() != active_dir.resolve() and builtin_dir.is_dir():
        scan_dirs.append(builtin_dir)

    for sdir in scan_dirs:
        if not sdir.is_dir():
            continue
        for md_file in sorted(sdir.glob("*.md")):
            if md_file.name in seen_ids or md_file.name.startswith("."):
                continue
            seen_ids.add(md_file.name)
            raw = md_file.read_text(encoding="utf-8")
            custom_meta, body = _parse_custom_meta(raw)
            stage = custom_meta.get("stage", "summary")
            if stage not in ("cleanup", "summary"):
                stage = "summary"
            cat = custom_meta.get("category", "custom")
            entry = {
                "id": md_file.name,
                "stage": stage,
                "name": custom_meta.get("name") or f"自訂：{md_file.stem}",
                "category": cat,
                "category_label": CATEGORY_LABELS.get(cat, "自訂模板"),
                "source": "使用者自訂",
                "description": custom_meta.get("description") or "使用者建立的客製化提示詞模板。",
                "is_builtin": False,
                "is_modified": False,
                "content": body,
            }
            if stage == "cleanup":
                cleanup_templates.append(entry)
            else:
                summary_templates.append(entry)

    return {
        "cleanup_templates": cleanup_templates,
        "summary_templates": summary_templates,
        "presets": list(SCENARIO_PRESETS),
        "category_labels": dict(CATEGORY_LABELS),
    }


def save_prompt_template(
    template_id: str,
    stage: str,
    name: str,
    category: str,
    description: str,
    content: str,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create a custom prompt template or update an active override copy."""
    clean_id = _sanitize_template_id(template_id)
    body = _strip_meta_if_any(content or "").strip()
    if not body:
        return {"status": "error", "message": "提示詞內容不可為空"}

    active_dir = get_active_prompts_dir(base_dir)
    active_dir.mkdir(parents=True, exist_ok=True)
    builtin_dir = get_builtin_prompts_dir()
    target_file = active_dir / clean_id
    is_builtin = clean_id in _BUILTIN_MAP

    if is_builtin:
        # Preserve original factory text if editing directly in-place on repo root
        if active_dir.resolve() == builtin_dir.resolve() and target_file.is_file():
            factory_dir = active_dir / ".factory_defaults"
            factory_bak = factory_dir / clean_id
            if not factory_bak.is_file():
                factory_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target_file, factory_bak)
        target_file.write_text(body + "\n", encoding="utf-8")
        return {
            "status": "ok",
            "template_id": clean_id,
            "stage": _BUILTIN_MAP[clean_id]["stage"],
            "is_builtin": True,
            "message": f"已儲存提示詞模板「{_BUILTIN_MAP[clean_id]['name']}」",
        }

    norm_stage = stage.strip().lower() if stage in ("cleanup", "summary") else "summary"
    norm_cat = category.strip() if category in CATEGORY_LABELS else "custom"
    meta_header = json.dumps(
        {
            "name": (name or clean_id[:-3]).strip(),
            "stage": norm_stage,
            "category": norm_cat,
            "description": (description or "使用者建立的客製化提示詞模板。").strip(),
        },
        ensure_ascii=False,
    )
    target_file.write_text(f"<!-- META: {meta_header} -->\n{body}\n", encoding="utf-8")
    return {
        "status": "ok",
        "template_id": clean_id,
        "stage": norm_stage,
        "is_builtin": False,
        "message": f"已儲存自訂提示詞模板「{(name or clean_id).strip()}」",
    }


def reset_prompt_template(
    template_id: str,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Restore a built-in prompt template back to its factory default content."""
    clean_id = _sanitize_template_id(template_id)
    if clean_id not in _BUILTIN_MAP:
        return {"status": "error", "message": "僅內建模板支援還原出廠預設"}

    active_dir = get_active_prompts_dir(base_dir)
    builtin_dir = get_builtin_prompts_dir()
    active_file = active_dir / clean_id

    if active_dir.resolve() != builtin_dir.resolve():
        # Remove isolated/installed override so factory built-in copy takes effect
        if active_file.is_file():
            active_file.unlink()
            builtin_copy = builtin_dir / clean_id
            if builtin_copy.is_file() and (active_dir / ".keep_copy").exists():
                shutil.copy2(builtin_copy, active_file)
        return {
            "status": "ok",
            "template_id": clean_id,
            "message": f"已還原「{_BUILTIN_MAP[clean_id]['name']}」為預設提示詞",
        }

    factory_bak = active_dir / ".factory_defaults" / clean_id
    if factory_bak.is_file():
        shutil.copy2(factory_bak, active_file)
        factory_bak.unlink()
    return {
        "status": "ok",
        "template_id": clean_id,
        "message": f"已還原「{_BUILTIN_MAP[clean_id]['name']}」為預設提示詞",
    }


def delete_custom_prompt_template(
    template_id: str,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Delete a user-created custom prompt template (refuses deleting built-ins)."""
    clean_id = _sanitize_template_id(template_id)
    if clean_id in _BUILTIN_MAP:
        return {"status": "error", "message": "無法刪除系統內建模板，請改用「還原預設」"}

    active_dir = get_active_prompts_dir(base_dir)
    target_file = active_dir / clean_id
    if not target_file.is_file():
        return {"status": "error", "message": f"找不到自訂模板：{clean_id}"}

    target_file.unlink()
    return {
        "status": "ok",
        "template_id": clean_id,
        "message": f"已刪除自訂提示詞模板：{clean_id}",
    }
