"""Bridge layer connecting FastAPI GUI with lecture pipeline backend.

Handles config persistence (remembering GUI execution mode), system diagnostics,
timetable CRUD, lecture history queries, background task execution, and WebSocket log streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml
from starlette.websockets import WebSocket, WebSocketState

from lecture_pipeline.state import load_config
from lecture_pipeline.automation.offline_queue import is_internet_connected
from lecture_pipeline.automation.watcher import check_disk_space, run_pipeline_for_group


CONFIG_FILE_PATH = Path("config.yaml")


class EventBroadcaster:
    """Manages connected WebSockets and broadcasts live progress, logs, and events."""

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self.lock = threading.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def register_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        with self.lock:
            self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        with self.lock:
            self.active_connections.discard(websocket)

    def broadcast_sync(self, message: Dict[str, Any]) -> None:
        """Thread-safe synchronous broadcast to all active WebSockets."""
        if not self.loop or self.loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        dead_connections: List[WebSocket] = []
        with self.lock:
            connections = list(self.active_connections)

        for conn in connections:
            if conn.client_state == WebSocketState.CONNECTED:
                try:
                    await conn.send_text(payload)
                except Exception:
                    dead_connections.append(conn)
            else:
                dead_connections.append(conn)

        if dead_connections:
            with self.lock:
                for dead in dead_connections:
                    self.active_connections.discard(dead)


broadcaster = EventBroadcaster()


# ---------------------------------------------------------------------------
# Config & Execution Mode Persistence
# ---------------------------------------------------------------------------

def get_current_config() -> Dict[str, Any]:
    """Load configuration from config.yaml with defaults."""
    cfg = load_config(CONFIG_FILE_PATH)
    if "gui" not in cfg:
        cfg["gui"] = {
            "mode": "window",
            "host": "127.0.0.1",
            "port": 8000,
            "auto_open_browser": True,
            "window_width": 1280,
            "window_height": 800,
        }
    return cfg


def get_remembered_gui_mode() -> str:
    """Return remembered execution mode ('window', 'web', 'both') from config.yaml."""
    cfg = get_current_config()
    gui_cfg = cfg.get("gui", {})
    mode = str(gui_cfg.get("mode", "window")).lower().strip()
    return mode if mode in {"window", "web", "both"} else "window"


def save_remembered_gui_mode(mode: str) -> str:
    """Save user-selected GUI mode to config.yaml so next launch remembers it."""
    valid_mode = mode.lower().strip()
    if valid_mode not in {"window", "web", "both"}:
        valid_mode = "window"

    if not CONFIG_FILE_PATH.is_file():
        return valid_mode

    try:
        content = CONFIG_FILE_PATH.read_text(encoding="utf-8")
        # Replace existing gui.mode
        pattern = r"(gui:\s*\n(?:\s+#[^\n]*\n)*\s+mode:\s*[\"']?)[a-zA-Z0-9_]+([\"']?)"
        if re.search(pattern, content):
            new_content = re.sub(pattern, rf"\g<1>{valid_mode}\g<2>", content)
        else:
            # Append if not found
            new_content = content + f"\n\ngui:\n  mode: \"{valid_mode}\"\n  host: \"127.0.0.1\"\n  port: 8000\n"

        CONFIG_FILE_PATH.write_text(new_content, encoding="utf-8")
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to update mode in config.yaml: {e}")

    return valid_mode


def switch_gui_mode_action(mode: str, source: str = "browser") -> Dict[str, Any]:
    """Switch execution mode, persist to config.yaml, and trigger window/browser actions."""
    saved_mode = save_remembered_gui_mode(mode)
    cfg = get_current_config()
    gui_cfg = cfg.get("gui", {})
    server_host = gui_cfg.get("host", "127.0.0.1")
    server_port = int(gui_cfg.get("port", 8000))
    url = f"http://{server_host}:{server_port}"

    from lecture_pipeline.gui.window import hide_desktop_window, show_desktop_window
    import webbrowser

    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return {
            "status": "ok",
            "mode": saved_mode,
            "message": f"已切換為「{saved_mode}」模式。",
            "url": url,
        }

    if saved_mode == "web":
        # Only open external browser if user clicked this inside desktop window
        if source == "window":
            webbrowser.open(url)
        hide_desktop_window()
        return {
            "status": "ok",
            "mode": "web",
            "message": "已設定為「網頁模式」，桌面原生視窗已關閉。",
            "url": url,
        }
    elif saved_mode == "window":
        show_desktop_window()
        return {
            "status": "ok",
            "mode": "window",
            "message": "已開啟桌面原生視窗。",
            "url": url,
        }
    else:  # both
        show_desktop_window()
        if source == "window":
            webbrowser.open(url)
        return {
            "status": "ok",
            "mode": "both",
            "message": "已同時開啟瀏覽器與桌面原生視窗。",
            "url": url,
        }


def update_pipeline_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update configurable parameters in config.yaml."""
    cfg = load_config(CONFIG_FILE_PATH)
    for k, v in updates.items():
        if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
            cfg[k].update(v)
        else:
            cfg[k] = v

    try:
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to save config.yaml: {e}")

    return cfg


# ---------------------------------------------------------------------------
# Timetable Management (CRUD)
# ---------------------------------------------------------------------------

DAY_ZH_MAP = {
    "Monday": "週一",
    "Tuesday": "週二",
    "Wednesday": "週三",
    "Thursday": "週四",
    "Friday": "週五",
    "Saturday": "週六",
    "Sunday": "週日",
}


def get_timetable_rules() -> Dict[str, Any]:
    """Parse timetable entries from config.yaml into clean JSON rules."""
    cfg = load_config(CONFIG_FILE_PATH)
    tt_section = cfg.get("timetable", {})
    enabled = bool(tt_section.get("enabled", True))

    rules: List[Dict[str, Any]] = []
    rule_id = 1
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        day_rules = tt_section.get(day)
        if isinstance(day_rules, list):
            for r in day_rules:
                if isinstance(r, dict) and "time" in r and "course" in r:
                    rules.append({
                        "id": f"rule_{rule_id}",
                        "day": day,
                        "dayZh": DAY_ZH_MAP.get(day, day),
                        "time": str(r["time"]),
                        "course": str(r["course"]),
                    })
                    rule_id += 1

    return {"enabled": enabled, "rules": rules}


def save_timetable_rules(rules: List[Dict[str, Any]], enabled: bool = True) -> Dict[str, Any]:
    """Save timetable rules back into config.yaml."""
    cfg = load_config(CONFIG_FILE_PATH)
    new_tt: Dict[str, Any] = {"enabled": enabled}

    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        new_tt[day] = []

    for r in rules:
        day = r.get("day", "Monday")
        if day in new_tt:
            new_tt[day].append({
                "time": str(r.get("time", "09:00-11:00")),
                "course": str(r.get("course", "")),
            })

    # Clean up empty days to None for clean YAML
    for day in list(new_tt.keys()):
        if day != "enabled" and not new_tt[day]:
            new_tt[day] = None

    cfg["timetable"] = new_tt
    try:
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to update timetable: {e}")

    return get_timetable_rules()


# ---------------------------------------------------------------------------
# System Status & Diagnostics
# ---------------------------------------------------------------------------

_watcher_paused = False


def is_watcher_paused() -> bool:
    global _watcher_paused
    return _watcher_paused


def set_watcher_paused(paused: bool) -> bool:
    global _watcher_paused
    _watcher_paused = paused
    broadcaster.broadcast_sync({
        "type": "watcher_state",
        "paused": _watcher_paused,
        "badge": "已暫停" if _watcher_paused else "常駐待命",
    })
    return _watcher_paused


def get_full_system_status() -> Dict[str, Any]:
    """Collect real hardware, network, token, and watcher diagnostics."""
    # 1. GPU Check
    gpu_name = "未偵測到 GPU"
    vram_free = "0 MB"
    gpu_driver = "未知"
    gpu_ok = False
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=gpu_name,memory.free,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if smi.returncode == 0 and smi.stdout.strip():
            parts = [p.strip() for p in smi.stdout.strip().split(",")]
            if len(parts) >= 3:
                gpu_name = parts[0]
                free_mb = int(parts[1])
                vram_free = f"{round(free_mb / 1024, 1)} GB"
                gpu_driver = parts[2]
                gpu_ok = True
    except Exception:
        pass

    # 2. Internet Connection
    net_ok = is_internet_connected(timeout=2.0)

    # 3. Google Docs OAuth status
    token_file = Path("token.json")
    cred_file = Path("credentials.json")
    docs_authorized = token_file.is_file() and cred_file.is_file()

    # 4. Disk space
    cfg = get_current_config()
    paths = cfg.get("paths", {})
    inbox_dir = paths.get("inbox_dir", "inbox")
    disk_ok, disk_free_gb = check_disk_space(inbox_dir, min_free_gb=2.0)

    # 5. Gemini API Key check
    gemini_key_set = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    return {
        "mode": get_remembered_gui_mode(),
        "gpu": {
            "ok": gpu_ok,
            "name": gpu_name,
            "vram_free": vram_free,
            "driver": gpu_driver,
            "label": f"{gpu_name} ({vram_free})" if gpu_ok else "本機 CPU 運算",
        },
        "network": {
            "ok": net_ok,
            "label": "在線 / 額度充裕" if net_ok else "離線模式 (自動排隊)",
        },
        "google_docs": {
            "ok": docs_authorized,
            "label": "Google Docs 已授權" if docs_authorized else "尚未設定 OAuth",
        },
        "api_key": {
            "ok": gemini_key_set,
            "label": "Gemini API 金鑰就緒" if gemini_key_set else "缺少 GEMINI_API_KEY",
        },
        "disk": {
            "ok": disk_ok,
            "free_gb": disk_free_gb,
        },
        "watcher": {
            "paused": is_watcher_paused(),
            "badge": "已暫停" if is_watcher_paused() else "常駐待命",
        },
        "active_task": get_current_task_status(),
    }


# ---------------------------------------------------------------------------
# History & Lecture Library
# ---------------------------------------------------------------------------

_lecture_cache: Dict[str, Path] = {}
_drive_links_cache: Dict[str, str] = {}


def _get_drive_link_from_logs(course_or_id: str) -> Optional[str]:
    """Extract Google Drive link for a lecture or course from watcher.log if available."""
    if course_or_id in _drive_links_cache:
        return _drive_links_cache[course_or_id]

    log_file = Path("logs/watcher.log")
    if not log_file.is_file():
        return None

    try:
        content = log_file.read_text(encoding="utf-8", errors="ignore")
        matches = re.findall(r"已同步至 Google Drive：(https://drive\.google\.com/[^\s\r\n]+)", content)
        if matches:
            link = matches[-1]
            _drive_links_cache[course_or_id] = link
            return link
    except Exception:
        pass
    return None


def get_archived_lectures() -> List[Dict[str, Any]]:
    """Recursively scan exports, archive, work, and record directories for processed lecture bundles."""
    global _lecture_cache
    cfg = get_current_config()
    paths = cfg.get("paths", {})
    archive_dir = Path(paths.get("archive_dir", "archive"))
    work_dir = Path(paths.get("work_dir", "work"))
    export_dir = Path(cfg.get("export", {}).get("export_dir", "exports")).resolve()
    rec_archive_str = paths.get("record_archive_dir")
    rec_archive = Path(rec_archive_str) if rec_archive_str else None

    search_dirs = [export_dir, work_dir, archive_dir]
    if rec_archive and rec_archive.is_dir():
        search_dirs.append(rec_archive)

    lectures: List[Dict[str, Any]] = []
    seen_dirs: Set[str] = set()
    new_cache: Dict[str, Path] = {}

    today_str = time.strftime("%Y-%m-%d")
    today_compact = time.strftime("%Y%m%d")

    for base in search_dirs:
        if not base.is_dir():
            continue

        candidate_dirs: List[Path] = []
        try:
            for p in base.rglob("*"):
                if not p.is_dir():
                    continue
                if p.name.startswith(".") or p.name in ["chunks", "__pycache__", "node_modules", "static", "templates"]:
                    continue
                has_summary = (p / "03_summary.md").is_file() or any(f.is_file() for f in p.glob("*重點總結筆記.md")) or any(f.is_file() for f in p.glob("*筆記*.md"))
                has_cleaned = (p / "02_cleaned.txt").is_file() or any(f.is_file() for f in p.glob("*完整課堂整理文稿.md"))
                has_raw = (p / "01_raw.txt").is_file() or (p / "01_raw_timestamped.txt").is_file() or any(f.is_file() for f in p.glob("*原始時間戳對照.md"))
                has_run = (p / "run.json").is_file()
                has_audio = any(f.is_file() for ext in [".mp3", ".opus", ".wav", ".flac", ".m4a"] for f in p.glob(f"*{ext}"))
                if has_summary or has_cleaned or has_raw or has_run or has_audio:
                    canonical = str(p.resolve())
                    if canonical not in seen_dirs:
                        candidate_dirs.append(p)
                        seen_dirs.add(canonical)
        except Exception as e:
            logging.getLogger("lecture_pipeline.gui").warning(f"Error scanning {base}: {e}")

        # Sort candidate directories newest first
        candidate_dirs.sort(key=lambda d: d.stat().st_mtime if d.exists() else 0, reverse=True)

        for item in candidate_dirs:
            parent_name = item.parent.name
            if parent_name and parent_name not in ["work", "archive", "exports", base.name]:
                lecture_id = f"{parent_name}_{item.name}"
            else:
                lecture_id = item.name

            lecture_id_clean = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fa5\-]", "_", lecture_id)
            new_cache[lecture_id_clean] = item
            new_cache[lecture_id] = item
            new_cache[item.name] = item

            summary_file = item / "03_summary.md"
            if not summary_file.is_file():
                for sf in list(item.glob("*重點總結筆記.md")) + list(item.glob("*筆記*.md")) + list(item.glob("*.md")):
                    if sf.is_file():
                        summary_file = sf
                        break

            cleaned_file = item / "02_cleaned.txt"
            if not cleaned_file.is_file():
                for cf in list(item.glob("*完整課堂整理文稿.md")) + list(item.glob("*整理文稿*.md")):
                    if cf.is_file():
                        cleaned_file = cf
                        break

            raw_file = item / "01_raw.txt"
            raw_ts_file = item / "01_raw_timestamped.txt"
            if not raw_ts_file.is_file():
                for rf in list(item.glob("*原始時間戳對照.md")) + list(item.glob("*.txt")):
                    if rf.is_file():
                        raw_ts_file = rf
                        break

            run_json = item / "run.json"

            course_name = ""
            date_str = ""
            channel_name = "C1 (Flash)"
            docs_url = None
            if run_json.is_file():
                try:
                    with open(run_json, "r", encoding="utf-8") as rf:
                        rdata = json.load(rf)
                        course_name = rdata.get("course", "")
                        raw_date = str(rdata.get("date", ""))
                        if len(raw_date) == 8:
                            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                        docs_url = rdata.get("docs_url") or rdata.get("drive_link")
                except Exception:
                    pass

            if not course_name:
                if parent_name and parent_name not in ["work", "archive", "exports", base.name]:
                    course_name = parent_name
                else:
                    m = re.match(r"^(\d{6,8})_(.+)$", item.name)
                    if m:
                        course_name = m.group(2)
                    else:
                        course_name = item.name

            if not date_str:
                m_date = re.search(r"(20\d{6}|\d{6})", item.name)
                if m_date:
                    d = m_date.group(1)
                    if len(d) == 8:
                        date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                    elif len(d) == 6:
                        date_str = f"20{d[:2]}-{d[2:4]}-{d[4:6]}"
                else:
                    date_str = time.strftime("%Y-%m-%d", time.localtime(item.stat().st_mtime))

            if not docs_url:
                docs_url = _get_drive_link_from_logs(course_name)

            audio_path = None
            for ext in [".flac", ".opus", ".mp3", ".wav", ".m4a"]:
                matched = list(item.glob(f"*{ext}"))
                if matched:
                    matched.sort(key=lambda f: (not f.name.startswith("00_normalized"), f.name.startswith("chunk"), len(f.name)))
                    audio_path = matched[0]
                    break

            if not audio_path:
                for base_arch in [archive_dir, work_dir, rec_archive]:
                    if not base_arch or not base_arch.is_dir():
                        continue
                    patterns = []
                    if course_name:
                        patterns.append(f"**/*{course_name}*/*.*")
                    if date_str:
                        clean_d = date_str.replace("-", "")
                        patterns.append(f"**/*{clean_d}*/*.*")
                    for pat in patterns:
                        for mf in base_arch.glob(pat):
                            if mf.is_file() and mf.suffix.lower() in [".mp3", ".opus", ".wav", ".flac", ".m4a"]:
                                audio_path = mf
                                break
                        if audio_path:
                            break
                    if audio_path:
                        break

            summary_text = summary_file.read_text(encoding="utf-8") if summary_file.is_file() else ""
            cleaned_text = cleaned_file.read_text(encoding="utf-8") if cleaned_file.is_file() else ""
            raw_text = raw_ts_file.read_text(encoding="utf-8") if raw_ts_file.is_file() else (raw_file.read_text(encoding="utf-8") if raw_file.is_file() else "")

            mtime_date = time.strftime("%Y-%m-%d", time.localtime(item.stat().st_mtime))
            is_today = (mtime_date == today_str) or (date_str == today_str) or (today_compact in item.name)

            display_title = course_name if course_name else item.name
            lectures.append({
                "id": lecture_id_clean,
                "title": display_title,
                "course": course_name,
                "date": date_str,
                "is_today": is_today,
                "path": str(item.resolve()),
                "has_summary": summary_file.is_file(),
                "has_cleaned": cleaned_file.is_file(),
                "has_raw": bool(raw_text),
                "has_audio": audio_path is not None,
                "audio_filename": audio_path.name if audio_path else None,
                "audio_path": str(audio_path.resolve()) if audio_path else None,
                "audio_url": f"/api/audio/{lecture_id_clean}" if audio_path else None,
                "summary_preview": summary_text[:200] if summary_text else "",
                "char_count": len(cleaned_text) or len(raw_text),
                "channel": channel_name,
                "docs_url": docs_url,
            })

    _lecture_cache = new_cache
    return lectures


def get_lecture_content(lecture_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full text files and audio metadata for a specific lecture."""
    global _lecture_cache
    if not _lecture_cache:
        get_archived_lectures()

    target_dir = _lecture_cache.get(lecture_id)
    if not target_dir or not target_dir.is_dir():
        get_archived_lectures()
        target_dir = _lecture_cache.get(lecture_id)

    if not target_dir or not target_dir.is_dir():
        cfg = get_current_config()
        paths = cfg.get("paths", {})
        export_dir = Path(cfg.get("export", {}).get("export_dir", "exports")).resolve()
        for base in [export_dir, Path(paths.get("work_dir", "work")), Path(paths.get("archive_dir", "archive"))]:
            for p in base.rglob(f"*{lecture_id}*"):
                if p.is_dir():
                    target_dir = p
                    break
            if target_dir:
                break

    if not target_dir or not target_dir.is_dir():
        return None

    summary_file = target_dir / "03_summary.md"
    if not summary_file.is_file():
        for sf in list(target_dir.glob("*重點總結筆記.md")) + list(target_dir.glob("*筆記*.md")) + list(target_dir.glob("*.md")):
            if sf.is_file():
                summary_file = sf
                break

    cleaned_file = target_dir / "02_cleaned.txt"
    if not cleaned_file.is_file():
        for cf in list(target_dir.glob("*完整課堂整理文稿.md")) + list(target_dir.glob("*整理文稿*.md")):
            if cf.is_file():
                cleaned_file = cf
                break

    raw_file = target_dir / "01_raw.txt"
    raw_ts_file = target_dir / "01_raw_timestamped.txt"
    if not raw_ts_file.is_file():
        for rf in list(target_dir.glob("*原始時間戳對照.md")) + list(target_dir.glob("*.txt")):
            if rf.is_file():
                raw_ts_file = rf
                break

    course_name = target_dir.parent.name if target_dir.parent.name not in ["work", "archive", "exports", "Record"] else target_dir.name
    date_match = re.search(r"(20\d{6}|\d{6})", target_dir.name)
    date_str = date_match.group(1) if date_match else ""

    audio_file = None
    for ext in [".flac", ".opus", ".mp3", ".wav", ".m4a"]:
        found = list(target_dir.glob(f"*{ext}"))
        if found:
            found.sort(key=lambda f: (not f.name.startswith("00_normalized"), f.name.startswith("chunk"), len(f.name)))
            audio_file = found[0]
            break

    if not audio_file:
        cfg = get_current_config()
        paths = cfg.get("paths", {})
        archive_dir = Path(paths.get("archive_dir", "archive"))
        work_dir = Path(paths.get("work_dir", "work"))
        rec_archive_str = paths.get("record_archive_dir")
        rec_archive = Path(rec_archive_str) if rec_archive_str else None
        for base_arch in [archive_dir, work_dir, rec_archive]:
            if not base_arch or not base_arch.is_dir():
                continue
            for pat in [f"**/*{course_name}*/*.*", f"**/*{date_str}*/*.*" if date_str else ""]:
                if not pat:
                    continue
                for mf in base_arch.glob(pat):
                    if mf.is_file() and mf.suffix.lower() in [".mp3", ".opus", ".wav", ".flac", ".m4a"]:
                        audio_file = mf
                        break
                if audio_file:
                    break
            if audio_file:
                break

    docs_url = _get_drive_link_from_logs(course_name) or _get_drive_link_from_logs(target_dir.name)

    return {
        "id": lecture_id,
        "title": target_dir.name,
        "course": course_name,
        "path": str(target_dir.resolve()),
        "summary": summary_file.read_text(encoding="utf-8") if summary_file.is_file() else "",
        "cleaned": cleaned_file.read_text(encoding="utf-8") if cleaned_file.is_file() else "",
        "raw": raw_ts_file.read_text(encoding="utf-8") if raw_ts_file.is_file() else (raw_file.read_text(encoding="utf-8") if raw_file.is_file() else ""),
        "has_audio": audio_file is not None,
        "audio_url": f"/api/audio/{lecture_id}" if audio_file else None,
        "audio_path": str(audio_file.resolve()) if audio_file else None,
        "docs_url": docs_url,
    }


def resolve_export_lecture_file(lecture_id: str, file_type: str = "summary") -> Optional[Path]:
    """Resolve target note file directed to the Exports directory (筆記與播放器匯出目錄)."""
    cfg = get_current_config()
    export_dir = Path(cfg.get("export", {}).get("export_dir", "exports")).resolve()
    export_dir.mkdir(parents=True, exist_ok=True)

    detail = get_lecture_content(lecture_id)
    course = (detail.get("course") if detail else "") or ""
    title = (detail.get("title") if detail else "") or ""
    date_match = re.search(r"(20\d{6}|\d{6})", f"{lecture_id}_{title}")
    date_str = date_match.group(1) if date_match else ""

    patterns = []
    if file_type == "summary":
        patterns = ["*重點總結筆記.md", "*重點筆記*.md", "*筆記*.md", "*summary*.md", "01_*.md", "*.md"]
    elif file_type == "cleaned":
        patterns = ["*完整課堂整理文稿.md", "*整理文稿*.md", "02_*.md", "*.txt"]
    elif file_type == "raw":
        patterns = ["*原始時間戳對照.md", "*時間戳*.md", "03_*.md", "*.txt"]
    else:
        patterns = ["*.md", "*.txt"]

    # 1. Look in export_dir / course / date_str
    if course and date_str:
        c_dir = export_dir / course / date_str
        if c_dir.is_dir():
            for pat in patterns:
                for f in c_dir.glob(pat):
                    if f.is_file():
                        return f

    # 2. Look in export_dir / course
    if course:
        c_dir = export_dir / course
        if c_dir.is_dir():
            for pat in patterns:
                for f in c_dir.rglob(pat):
                    if f.is_file() and (not date_str or date_str in f.name or date_str in str(f.parent)):
                        return f

    # 3. Look in export_dir for matching lecture_id or date
    for pat in patterns:
        for f in export_dir.rglob(pat):
            if f.is_file():
                if course and course.lower() in str(f).lower():
                    return f
                if lecture_id and lecture_id.lower() in str(f).lower():
                    return f

    # 4. If detail is already an exported file, return it
    if detail and detail.get("path"):
        d_path = Path(detail["path"])
        if str(export_dir).lower() in str(d_path).lower():
            if file_type == "summary":
                for sf in list(d_path.glob("*重點總結筆記.md")) + list(d_path.glob("*.md")):
                    if sf.is_file():
                        return sf

    # 5. Copy or create note in export_dir / course / date_str
    c_name = course or (lecture_id.split("_")[0] if "_" in lecture_id else "Lecture")
    d_name = date_str or time.strftime("%Y%m%d")
    target_exp_dir = export_dir / c_name / d_name
    target_exp_dir.mkdir(parents=True, exist_ok=True)

    dest_file = target_exp_dir / f"01_{c_name}_{d_name}_重點總結筆記.md"
    summary_text = (detail.get("summary") if detail else "") or ""
    if not summary_text and detail and detail.get("path"):
        src_sum = Path(detail["path"]) / "03_summary.md"
        if src_sum.is_file():
            summary_text = src_sum.read_text(encoding="utf-8")

    if not summary_text:
        summary_text = f"# 【課堂考前重點總結筆記】{c_name} ({d_name})\n\n（尚未產生筆記或處理中）"

    dest_file.write_text(summary_text, encoding="utf-8")
    return dest_file


# ---------------------------------------------------------------------------
# Native Windows Desktop Integration
# ---------------------------------------------------------------------------

def open_folder_in_desktop(folder_path: Path | str) -> bool:
    """Open target folder in Windows File Explorer or create if not existing."""
    p = Path(folder_path).resolve()
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return True
    try:
        if sys.platform == "win32":
            os.startfile(str(p))
            return True
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)])
            return True
        else:
            subprocess.run(["xdg-open", str(p)])
            return True
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to open folder {p}: {e}")
        return False


def open_file_in_desktop(file_path: Path | str) -> bool:
    """Open target file using system default application, with Notepad fallback."""
    p = Path(file_path).resolve()
    if not p.is_file():
        return False
    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return True
    try:
        if sys.platform == "win32":
            try:
                os.startfile(str(p))
                return True
            except OSError:
                subprocess.Popen(["notepad.exe", str(p)])
                return True
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)])
            return True
        else:
            subprocess.run(["xdg-open", str(p)])
            return True
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to open file {p}: {e}")
        try:
            if sys.platform == "win32":
                subprocess.Popen(["notepad.exe", str(p)])
                return True
        except Exception:
            pass
        return False


def browse_folder_dialog(initial_dir: Optional[str] = None) -> Optional[str]:
    """Launch native Windows directory selection dialog."""
    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return str(Path(initial_dir or os.getcwd()).resolve())
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            initialdir=initial_dir or os.getcwd(),
            title="選擇資料夾路徑",
        )
        root.destroy()
        return selected if selected else None
    except Exception:
        pass

    try:
        ps_cmd = (
            "[System.Reflection.Assembly]::LoadWithPartialName('System.windows.forms') | Out-Null;"
            "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$dlg.Description = '請選擇資料夾路徑';"
            "if($dlg.ShowDialog() -eq 'OK'){$dlg.SelectedPath}"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def browse_file_dialog(initial_dir: Optional[str] = None, file_types: Optional[List[tuple[str, str]]] = None) -> Optional[str]:
    """Launch native Windows file selection dialog."""
    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return str(Path(initial_dir or os.getcwd()).resolve())
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        types = file_types or [
            ("課程講義與文字 (*.pdf, *.txt, *.md)", "*.pdf;*.txt;*.md"),
            ("音訊檔案 (*.wav, *.mp3, *.m4a, *.flac, *.opus)", "*.wav;*.mp3;*.m4a;*.flac;*.opus;*.ogg"),
            ("所有檔案 (*.*)", "*.*"),
        ]
        selected = filedialog.askopenfilename(
            initialdir=initial_dir or os.getcwd(),
            title="請選擇檔案",
            filetypes=types,
        )
        root.destroy()
        return selected if selected else None
    except Exception:
        pass

    try:
        ps_cmd = (
            "[System.Reflection.Assembly]::LoadWithPartialName('System.windows.forms') | Out-Null;"
            "$dlg = New-Object System.Windows.Forms.OpenFileDialog;"
            "$dlg.Filter = '所有支援檔案 (*.pdf;*.txt;*.md;*.wav;*.mp3;*.m4a)|*.pdf;*.txt;*.md;*.wav;*.mp3;*.m4a|所有檔案 (*.*)|*.*';"
            "$dlg.Title = '請選擇檔案';"
            "if($dlg.ShowDialog() -eq 'OK'){$dlg.FileName}"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Background Task Execution with Real Pause & Abort Controls
# ---------------------------------------------------------------------------

_processing_lock = threading.Lock()
_current_task_info: Dict[str, Any] = {
    "running": False,
    "paused": False,
    "aborted": False,
    "task_name": "",
    "stage": "",
    "progress": 0,
    "detail": "",
    "elapsed_seconds": 0,
}
_task_abort_event = threading.Event()
_task_pause_event = threading.Event()
_tray_instance: Optional[Any] = None


def set_global_tray(tray: Any) -> None:
    global _tray_instance
    _tray_instance = tray


def get_global_tray() -> Optional[Any]:
    global _tray_instance
    return _tray_instance


def get_current_task_status() -> Dict[str, Any]:
    global _current_task_info
    with _processing_lock:
        info = dict(_current_task_info)
        if info.get("running") and "start_time" in info:
            info["elapsed_seconds"] = int(time.time() - info["start_time"])
        return info


def is_processing_active() -> bool:
    return _current_task_info.get("running", False)


def pause_processing_task() -> Dict[str, Any]:
    global _current_task_info
    with _processing_lock:
        if not _current_task_info.get("running"):
            return {"status": "error", "message": "目前無執行中的任務"}
        new_paused = not _current_task_info.get("paused", False)
        _current_task_info["paused"] = new_paused
        if new_paused:
            _task_pause_event.set()
        else:
            _task_pause_event.clear()

    if _tray_instance:
        _tray_instance.update_status(
            state="paused" if new_paused else "processing",
            detail="任務已暫停" if new_paused else _current_task_info.get("task_name", "處理中"),
            progress="暫停中" if new_paused else f"{_current_task_info.get('progress', 50)}%",
        )

    broadcaster.broadcast_sync({
        "type": "task_pause_toggle",
        "paused": new_paused,
        "badge": "已暫停" if new_paused else "進行中",
    })
    return {"status": "ok", "paused": new_paused}


def abort_processing_task() -> Dict[str, Any]:
    global _current_task_info
    with _processing_lock:
        if not _current_task_info.get("running"):
            return {"status": "error", "message": "目前無執行中的任務"}
        _current_task_info["running"] = False
        _current_task_info["aborted"] = True
        _task_abort_event.set()

    if _tray_instance:
        _tray_instance.update_status(state="idle", detail="任務已中止", progress="等待音檔")

    broadcaster.broadcast_sync({
        "type": "task_aborted",
        "detail": "任務已由使用者強制熔斷中止。已處理之中間進度保留於暫存目錄。",
    })
    return {"status": "ok", "message": "任務已強制熔斷中止"}


def get_task_abort_event() -> threading.Event:
    return _task_abort_event


def get_task_pause_event() -> threading.Event:
    return _task_pause_event


def update_task_progress(stage: str, progress: int, detail: str, task_name: str = "", running: bool = True) -> None:
    """Update active task state and broadcast to all connected WebSocket GUI clients and tray."""
    global _current_task_info
    with _processing_lock:
        if running and not _current_task_info.get("running"):
            _current_task_info["start_time"] = time.time()
        _current_task_info.update({
            "running": running,
            "stage": stage,
            "progress": progress,
            "detail": detail,
        })
        if task_name:
            _current_task_info["task_name"] = task_name

    current_name = _current_task_info.get("task_name", "")
    if _tray_instance:
        _tray_instance.update_status(
            state="processing" if running else "idle",
            detail=current_name if running else "準備就緒",
            progress=f"{progress}% {stage}" if running else "等待音檔",
        )

    broadcaster.broadcast_sync({
        "type": "progress",
        "running": running,
        "task_name": current_name,
        "stage": stage,
        "progress": progress,
        "detail": detail,
    })


def notify_task_completed(lecture_id: str, detail: str = "") -> None:
    """Mark task completed, notify tray, and broadcast completion event to GUI clients."""
    global _current_task_info
    with _processing_lock:
        _current_task_info.update({
            "running": False,
            "stage": "常駐監控待命",
            "progress": 100,
            "detail": detail or f"課堂已成功歸檔至 {lecture_id}",
        })

    if _tray_instance:
        _tray_instance.increment_processed()
        _tray_instance.update_status(state="idle", detail="正在監看 Inbox", progress="等待音檔")

    broadcaster.broadcast_sync({
        "type": "progress",
        "running": False,
        "progress": 100,
        "stage": "處理完成",
        "detail": detail or f"課堂已成功歸檔至 {lecture_id}",
    })
    broadcaster.broadcast_sync({
        "type": "task_completed",
        "lecture_id": lecture_id,
    })


def notify_task_error(error_msg: str, task_name: str = "") -> None:
    """Mark task failed and broadcast error to GUI clients."""
    global _current_task_info
    with _processing_lock:
        _current_task_info.update({
            "running": False,
            "stage": "處理失敗",
            "progress": 0,
            "detail": f"管線執行異常：{error_msg}",
        })

    if _tray_instance:
        _tray_instance.update_status(state="idle", detail="處理失敗", progress="等待音檔")

    broadcaster.broadcast_sync({
        "type": "progress",
        "running": False,
        "progress": 0,
        "stage": "處理失敗",
        "detail": f"管線執行異常：{error_msg}",
    })


def start_processing_task(audio_files: List[Path], channel_id: str = "C1") -> bool:
    """Launch end-to-end lecture pipeline in background thread with WebSocket progress broadcasts."""
    global _current_task_info
    with _processing_lock:
        if _current_task_info.get("running", False):
            return False

        _task_abort_event.clear()
        _task_pause_event.clear()
        file_names = [f.name for f in audio_files]
        primary_name = file_names[0] if file_names else "lecture.wav"
        _current_task_info = {
            "running": True,
            "paused": False,
            "aborted": False,
            "task_name": primary_name,
            "files": file_names,
            "channel": channel_id,
            "stage": "準備前處理",
            "progress": 5,
            "detail": f"開始處理音訊檔案：{', '.join(file_names)}",
            "start_time": time.time(),
        }

    def _worker():
        global _current_task_info
        try:
            update_task_progress(
                stage="準備前處理",
                progress=5,
                detail=f"開始處理音訊檔案：{', '.join(file_names)}",
                task_name=primary_name,
                running=True,
            )

            cfg = get_current_config()
            paths = cfg.get("paths", {})
            archive_dir = paths.get("archive_dir", "archive")
            rec_archive = paths.get("record_archive_dir")
            export_dir = cfg.get("export", {}).get("export_dir", "exports")

            out_dir = run_pipeline_for_group(
                audio_files=audio_files,
                channel_id=channel_id,
                pre_flags=cfg.get("preprocessing", {}).get("hp", "100Hz"),
                cleanup_model=cfg.get("cleanup", {}).get("model", "gemini-3.5-flash-lite"),
                summary_model=cfg.get("summarize", {}).get("model", "gemini-3.5-flash-lite"),
                archive_dir=archive_dir,
                upload_drive=True,
                tray=_tray_instance,
                record_archive_base_dir=rec_archive,
                abort_event=_task_abort_event,
                export_dir=export_dir,
            )

            notify_task_completed(
                lecture_id=out_dir.name,
                detail=f"課堂已成功歸檔至 {out_dir.name}",
            )

        except InterruptedError as ie:
            if _tray_instance:
                _tray_instance.update_status(state="idle", detail="任務已中止", progress="等待音檔")
            broadcaster.broadcast_sync({
                "type": "progress",
                "running": False,
                "progress": 0,
                "stage": "已中止",
                "detail": "任務已由使用者強制熔斷中止。已處理之中間進度保留於暫存目錄。",
            })
            broadcaster.broadcast_sync({
                "type": "task_aborted",
                "detail": str(ie),
            })
        except Exception as e:
            notify_task_error(error_msg=str(e), task_name=primary_name)
        finally:
            with _processing_lock:
                _current_task_info = {"running": False, "paused": False, "aborted": False}

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    return True


# ---------------------------------------------------------------------------
# Glossary Management & PDF Extraction
# ---------------------------------------------------------------------------

def get_course_glossary_terms(course: str = "") -> List[Dict[str, Any]]:
    """Load extracted terms for course or empty list if no course specified/available."""
    if not course or not course.strip():
        return []

    from lecture_pipeline.glossary import load_course_glossary
    terms = load_course_glossary(course.strip())

    result: List[Dict[str, Any]] = []
    for t in terms:
        m = re.match(r"^([^\(（\r\n]+?)\s*[\(（](.+?)[\)）]$", t.strip())
        if m:
            result.append({"term": m.group(1).strip(), "translation": m.group(2).strip()})
        else:
            result.append({"term": t.strip(), "translation": ""})
    return result


def get_available_courses() -> List[str]:
    """Retrieve list of all courses configured under courses/ directory."""
    courses_dir = Path("courses")
    if not courses_dir.is_dir():
        return []
    courses = [p.name for p in courses_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    courses.sort()
    return courses


def extract_terms_from_uploaded_file(file_content: bytes, filename: str, course: str = "") -> List[Dict[str, Any]]:
    """Save temporary syllabus file, extract domain terms locally, and update course glossary."""
    from lecture_pipeline.glossary import extract_terms_from_file, update_course_glossary

    target_course = course.strip() or Path(filename).stem
    temp_dir = Path("courses") / target_course
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / filename
    temp_file.write_bytes(file_content)

    try:
        extracted = extract_terms_from_file(temp_file)
        if extracted:
            update_course_glossary(target_course, extracted)
        return get_course_glossary_terms(target_course)
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to extract glossary from {filename}: {e}")
        return get_course_glossary_terms(target_course)
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / filename
    temp_file.write_bytes(file_content)

    try:
        extracted = extract_terms_from_file(temp_file)
        if extracted:
            update_course_glossary(course, extracted)
        return get_course_glossary_terms(course)
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to extract glossary from {filename}: {e}")
        return get_course_glossary_terms(course)


# ---------------------------------------------------------------------------
# Lecture & Glossary Management CRUD (Real Data Control)
# ---------------------------------------------------------------------------

def rename_lecture(lecture_id: str, new_course: str = "", new_title: str = "") -> Dict[str, Any]:
    """Rename lecture metadata and title in run.json and summary note."""
    global _lecture_cache
    if not _lecture_cache:
        get_archived_lectures()

    target_dir = _lecture_cache.get(lecture_id)
    if not target_dir or not target_dir.is_dir():
        return {"status": "error", "message": f"找不到指定課堂目錄：{lecture_id}"}

    run_json = target_dir / "run.json"
    rdata: Dict[str, Any] = {}
    if run_json.is_file():
        try:
            with open(run_json, "r", encoding="utf-8") as rf:
                rdata = json.load(rf)
        except Exception:
            pass

    if new_course:
        rdata["course"] = new_course.strip()
    if new_title:
        rdata["title"] = new_title.strip()

    try:
        with open(run_json, "w", encoding="utf-8") as wf:
            json.dump(rdata, wf, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"status": "error", "message": f"寫入 run.json 失敗：{e}"}

    # Invalidate cache so changes reflect immediately
    _lecture_cache.clear()
    get_archived_lectures()

    return {
        "status": "ok",
        "message": f"課堂資訊已成功更新",
        "lecture": get_lecture_content(lecture_id),
    }


def delete_lecture(lecture_id: str) -> Dict[str, Any]:
    """Safely delete archived lecture directory and cache."""
    global _lecture_cache
    if not _lecture_cache:
        get_archived_lectures()

    target_dir = _lecture_cache.get(lecture_id)
    if not target_dir or not target_dir.is_dir():
        return {"status": "error", "message": f"找不到指定課堂目錄：{lecture_id}"}

    try:
        shutil.rmtree(str(target_dir))
        _lecture_cache.pop(lecture_id, None)
        _lecture_cache.clear()
        return {"status": "ok", "message": "課堂紀錄與相關產物已安全刪除"}
    except Exception as e:
        return {"status": "error", "message": f"刪除課堂目錄失敗：{e}"}


def add_course(course: str) -> Dict[str, Any]:
    """Create a new course directory and glossary file."""
    clean_course = re.sub(r'[\/\\:\*\?"<>\|]', '_', course).strip()
    if not clean_course:
        return {"status": "error", "message": "科目名稱不可為空"}

    course_dir = Path("courses") / clean_course
    course_dir.mkdir(parents=True, exist_ok=True)
    glossary_file = course_dir / "glossary.txt"
    if not glossary_file.is_file():
        glossary_file.write_text(f"# Course glossary for {clean_course}\n", encoding="utf-8")

    return {
        "status": "ok",
        "course": clean_course,
        "courses": get_available_courses(),
        "message": f"已成功新增科目：{clean_course}",
    }


def delete_course(course: str) -> Dict[str, Any]:
    """Delete a course directory and its glossary."""
    course_dir = Path("courses") / course
    if not course_dir.is_dir():
        return {"status": "error", "message": f"找不到科目：{course}"}

    try:
        shutil.rmtree(str(course_dir))
        return {
            "status": "ok",
            "courses": get_available_courses(),
            "message": f"科目 {course} 已移除",
        }
    except Exception as e:
        return {"status": "error", "message": f"刪除科目失敗：{e}"}


def add_glossary_term(course: str, term: str, translation: str = "") -> Dict[str, Any]:
    """Add a domain term to course glossary."""
    clean_term = term.strip()
    if not clean_term:
        return {"status": "error", "message": "名詞不可為空"}

    course_dir = Path("courses") / course
    course_dir.mkdir(parents=True, exist_ok=True)
    glossary_file = course_dir / "glossary.txt"

    entry = f"{clean_term} ({translation.strip()})" if translation.strip() else clean_term

    with open(glossary_file, "a", encoding="utf-8") as f:
        f.write(f"\n{entry}\n")

    return {
        "status": "ok",
        "course": course,
        "terms": get_course_glossary_terms(course),
        "message": f"已新增專有名詞：{entry}",
    }


def edit_glossary_term(course: str, old_term: str, new_term: str, new_translation: str = "") -> Dict[str, Any]:
    """Edit an existing term in course glossary."""
    course_dir = Path("courses") / course
    glossary_file = course_dir / "glossary.txt"
    if not glossary_file.is_file():
        return {"status": "error", "message": "詞庫檔不存在"}

    lines = glossary_file.read_text(encoding="utf-8").splitlines()
    updated_lines = []
    replaced = False

    new_entry = f"{new_term.strip()} ({new_translation.strip()})" if new_translation.strip() else new_term.strip()

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and old_term.strip().lower() in stripped.lower() and not replaced:
            updated_lines.append(new_entry)
            replaced = True
        else:
            updated_lines.append(line)

    if not replaced:
        updated_lines.append(new_entry)

    glossary_file.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    return {
        "status": "ok",
        "course": course,
        "terms": get_course_glossary_terms(course),
        "message": f"名詞已更新為：{new_entry}",
    }


def delete_glossary_term(course: str, term: str) -> Dict[str, Any]:
    """Delete a domain term from course glossary."""
    course_dir = Path("courses") / course
    glossary_file = course_dir / "glossary.txt"
    if not glossary_file.is_file():
        return {"status": "error", "message": "詞庫檔不存在"}

    lines = glossary_file.read_text(encoding="utf-8").splitlines()
    updated_lines = []
    target = term.strip().lower()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            updated_lines.append(line)
        elif target in stripped.lower():
            continue
        else:
            updated_lines.append(line)

    glossary_file.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    return {
        "status": "ok",
        "course": course,
        "terms": get_course_glossary_terms(course),
        "message": f"已刪除專有名詞：{term}",
    }
