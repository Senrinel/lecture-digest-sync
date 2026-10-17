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
    """Recursively scan archive, work, and record directories for processed lecture bundles."""
    global _lecture_cache
    cfg = get_current_config()
    paths = cfg.get("paths", {})
    archive_dir = Path(paths.get("archive_dir", "archive"))
    work_dir = Path(paths.get("work_dir", "work"))
    rec_archive_str = paths.get("record_archive_dir")
    rec_archive = Path(rec_archive_str) if rec_archive_str else None

    search_dirs = [work_dir, archive_dir]
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
                has_summary = (p / "03_summary.md").is_file()
                has_cleaned = (p / "02_cleaned.txt").is_file()
                has_raw = (p / "01_raw.txt").is_file()
                has_run = (p / "run.json").is_file()
                if has_summary or has_cleaned or has_raw or has_run:
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
            if parent_name and parent_name not in ["work", "archive", base.name]:
                lecture_id = f"{parent_name}_{item.name}"
            else:
                lecture_id = item.name

            lecture_id_clean = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fa5\-]", "_", lecture_id)
            new_cache[lecture_id_clean] = item
            new_cache[lecture_id] = item
            new_cache[item.name] = item

            summary_file = item / "03_summary.md"
            cleaned_file = item / "02_cleaned.txt"
            raw_file = item / "01_raw.txt"
            raw_ts_file = item / "01_raw_timestamped.txt"
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
                if parent_name and parent_name not in ["work", "archive", base.name]:
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

            if not audio_path and course_name:
                for base_arch in [archive_dir, work_dir]:
                    matched = list(base_arch.glob(f"**/{course_name}*/*.*"))
                    for mf in matched:
                        if mf.suffix.lower() in [".mp3", ".opus", ".wav", ".flac", ".m4a"]:
                            audio_path = mf
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
        for base in [Path(paths.get("work_dir", "work")), Path(paths.get("archive_dir", "archive"))]:
            for p in base.rglob(lecture_id):
                if p.is_dir():
                    target_dir = p
                    break
            if target_dir:
                break

    if not target_dir or not target_dir.is_dir():
        return None

    summary_file = target_dir / "03_summary.md"
    cleaned_file = target_dir / "02_cleaned.txt"
    raw_file = target_dir / "01_raw.txt"
    raw_ts_file = target_dir / "01_raw_timestamped.txt"
    run_json = target_dir / "run.json"

    audio_file = None
    for ext in [".flac", ".opus", ".mp3", ".wav", ".m4a"]:
        found = list(target_dir.glob(f"*{ext}"))
        if found:
            found.sort(key=lambda f: (not f.name.startswith("00_normalized"), f.name.startswith("chunk"), len(f.name)))
            audio_file = found[0]
            break

    course_name = target_dir.parent.name if target_dir.parent.name not in ["work", "archive"] else target_dir.name
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


# ---------------------------------------------------------------------------
# Native Windows Desktop Integration
# ---------------------------------------------------------------------------

def open_folder_in_desktop(folder_path: Path | str) -> bool:
    """Open target folder in Windows File Explorer or create if not existing."""
    p = Path(folder_path).resolve()
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
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
    """Open target file using system default application."""
    p = Path(file_path).resolve()
    if not p.is_file():
        return False
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
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to open file {p}: {e}")
        return False


def browse_folder_dialog(initial_dir: Optional[str] = None) -> Optional[str]:
    """Launch native Windows directory selection dialog."""
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

    broadcaster.broadcast_sync({
        "type": "task_aborted",
        "detail": "任務已由使用者強制熔斷中止。已處理之中間進度保留於暫存目錄。",
    })
    return {"status": "ok", "message": "任務已強制熔斷中止"}


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
            broadcaster.broadcast_sync({
                "type": "progress",
                "running": True,
                "task_name": primary_name,
                "progress": 5,
                "stage": "準備前處理",
                "detail": f"開始處理音訊檔案：{', '.join(file_names)}",
            })

            cfg = get_current_config()
            paths = cfg.get("paths", {})
            archive_dir = paths.get("archive_dir", "archive")
            rec_archive = paths.get("record_archive_dir")

            out_dir = run_pipeline_for_group(
                audio_files=audio_files,
                channel_id=channel_id,
                pre_flags=cfg.get("preprocessing", {}).get("hp", "100Hz"),
                cleanup_model=cfg.get("cleanup", {}).get("model", "gemini-3.5-flash-lite"),
                summary_model=cfg.get("summarize", {}).get("model", "gemini-3.5-flash-lite"),
                archive_dir=archive_dir,
                upload_drive=True,
                record_archive_base_dir=rec_archive,
            )

            broadcaster.broadcast_sync({
                "type": "progress",
                "running": False,
                "progress": 100,
                "stage": "處理完成",
                "detail": f"課堂已成功歸檔至 {out_dir.name}",
            })
            broadcaster.broadcast_sync({
                "type": "task_completed",
                "lecture_id": out_dir.name,
            })

        except Exception as e:
            broadcaster.broadcast_sync({
                "type": "progress",
                "running": False,
                "progress": 0,
                "stage": "處理失敗",
                "detail": f"管線執行異常：{str(e)}",
            })
        finally:
            with _processing_lock:
                _current_task_info = {"running": False, "paused": False, "aborted": False}

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    return True


# ---------------------------------------------------------------------------
# Glossary Management & PDF Extraction
# ---------------------------------------------------------------------------

def get_course_glossary_terms(course: str = "Discrete Mathematics") -> List[Dict[str, Any]]:
    """Load extracted terms for course or default list."""
    from lecture_pipeline.glossary import load_course_glossary
    terms = load_course_glossary(course)
    if not terms:
        courses_dir = Path("courses")
        if courses_dir.is_dir():
            for c_dir in courses_dir.iterdir():
                if c_dir.is_dir():
                    c_terms = load_course_glossary(c_dir.name)
                    if c_terms:
                        terms = c_terms
                        break

    result: List[Dict[str, Any]] = []
    for t in terms:
        m = re.match(r"([A-Za-z0-9\s\-\_]+)\s*[\(（]([\u4e00-\u9fa5\s]+)[\)）]", t)
        if m:
            result.append({"term": m.group(1).strip(), "translation": m.group(2).strip()})
        else:
            result.append({"term": t, "translation": ""})
    return result


def get_available_courses() -> List[str]:
    """Retrieve list of all courses configured under courses/ directory."""
    courses_dir = Path("courses")
    if not courses_dir.is_dir():
        return ["Discrete Mathematics"]
    courses = [p.name for p in courses_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    courses.sort()
    return courses or ["Discrete Mathematics"]


def extract_terms_from_uploaded_file(file_content: bytes, filename: str, course: str = "Discrete Mathematics") -> List[Dict[str, Any]]:
    """Save temporary syllabus file, extract domain terms locally, and update course glossary."""
    from lecture_pipeline.glossary import extract_terms_from_file, update_course_glossary

    temp_dir = Path("courses") / course
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
