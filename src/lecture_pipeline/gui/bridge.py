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
from lecture_pipeline.utils import silent_popen, silent_run
from lecture_pipeline.automation.offline_queue import is_internet_connected
from lecture_pipeline.automation.watcher import check_disk_space, run_pipeline_for_group


def get_config_file_path() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "config.yaml").is_file():
            return exe_dir / "config.yaml"
    return Path("config.yaml")


CONFIG_FILE_PATH = get_config_file_path()


def _get_app_base_dir() -> Path:
    """Return base directory anchored to CONFIG_FILE_PATH or executable location."""
    try:
        cfg_path = Path(CONFIG_FILE_PATH)
        if cfg_path.is_absolute():
            return cfg_path.parent.resolve()
        if cfg_path.is_file():
            return cfg_path.resolve().parent
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def _resolve_app_path(target: Path | str) -> Path:
    """Resolve target path against app base directory when relative."""
    p = Path(os.path.expanduser(str(target)))
    if not p.is_absolute():
        p = _get_app_base_dir() / p
    return p.resolve()


def _clean_desktop_env() -> Dict[str, str]:
    """Return process environment cleaned of PyInstaller/Electron variables for desktop launches."""
    env = os.environ.copy()
    strip_dirs: List[str] = []
    for raw_dir in (
        getattr(sys, "_MEIPASS", None),
        env.get("_MEIPASS"),
        env.get("_MEIPASS2"),
    ):
        if raw_dir:
            norm = os.path.normcase(os.path.normpath(str(raw_dir))).rstrip("\\/")
            if norm and norm not in strip_dirs:
                strip_dirs.append(norm)

    for base_candidate in (
        Path(sys.executable).resolve().parent / "_internal",
        _get_app_base_dir() / "_internal",
    ):
        norm = os.path.normcase(os.path.normpath(str(base_candidate))).rstrip("\\/")
        if norm and norm not in strip_dirs:
            strip_dirs.append(norm)

    for key in ("_MEIPASS", "_MEIPASS2", "PYTHONHOME", "PYTHONPATH", "ELECTRON_RUN_AS_NODE"):
        env.pop(key, None)

    if strip_dirs:
        sep = os.path.normcase(os.sep)
        for path_key in [k for k in list(env.keys()) if k.upper() == "PATH"]:
            raw_path = env.get(path_key, "")
            if not raw_path:
                continue
            kept_parts: List[str] = []
            for part in raw_path.split(os.pathsep):
                if not part:
                    continue
                norm_part = os.path.normcase(os.path.normpath(part)).rstrip("\\/")
                if any(
                    norm_part == s or norm_part.startswith(s + sep)
                    for s in strip_dirs
                ):
                    continue
                kept_parts.append(part)
            env[path_key] = os.pathsep.join(kept_parts)

    return env


def _resolve_vscode_executable() -> Optional[str]:
    """Resolve native VS Code executable (`Code.exe` / `code`) avoiding `.cmd` shell wrappers."""
    if sys.platform != "win32":
        return shutil.which("code")

    candidates: List[Path] = []
    which_hit = shutil.which("code") or shutil.which("code.cmd")
    if which_hit:
        wp = Path(which_hit).resolve()
        if wp.suffix.lower() == ".exe":
            return str(wp)
        if wp.parent.name.lower() == "bin":
            candidates.append(wp.parent.parent / "Code.exe")

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Microsoft VS Code" / "Code.exe")
    for env_key, default_pf in (("ProgramFiles", r"C:\Program Files"), ("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        pf = os.environ.get(env_key, default_pf)
        if pf:
            candidates.append(Path(pf) / "Microsoft VS Code" / "Code.exe")

    for cand in candidates:
        try:
            if cand.is_file():
                return str(cand.resolve())
        except OSError:
            continue

    if which_hit and Path(which_hit).suffix.lower() in (".exe", ".com"):
        return str(Path(which_hit).resolve())
    return None


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
        CONFIG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE_PATH.write_text(
            f"gui:\n  mode: \"{valid_mode}\"\n  host: \"127.0.0.1\"\n  port: 8000\n",
            encoding="utf-8",
        )
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
            "message": "已設定為「網頁模式」，桌面視窗已關閉。",
            "url": url,
        }
    elif saved_mode == "window":
        show_desktop_window()
        return {
            "status": "ok",
            "mode": "window",
            "message": "已開啟桌面視窗。",
            "url": url,
        }
    else:  # both
        show_desktop_window()
        if source == "window":
            webbrowser.open(url)
        return {
            "status": "ok",
            "mode": "both",
            "message": "已同時開啟瀏覽器與桌面視窗。",
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
        "badge": "已暫停" if _watcher_paused else "待命",
    })
    return _watcher_paused


_oauth_status_cache: Dict[str, Any] = {
    "path": None,
    "mtime": None,
    "checked_at": 0.0,
    "ok": False,
    "label": "尚未設定 OAuth",
}


def _get_cached_google_auth_status(net_ok: bool) -> Dict[str, Any]:
    """Validate token.json with mtime/TTL cache so invalid_grant is surfaced accurately without spamming requests."""
    base_dir = CONFIG_FILE_PATH.parent
    token_file = (base_dir / "token.json").resolve()
    cred_file = (base_dir / "credentials.json").resolve()

    if not cred_file.is_file():
        return {"ok": False, "label": "尚未設定 OAuth (缺少 credentials.json)", "needs_reauth": True}
    if not token_file.is_file():
        return {"ok": False, "label": "尚未授權 (請登入 Google 帳號)", "needs_reauth": True}

    try:
        cur_mtime = token_file.stat().st_mtime
    except Exception:
        cur_mtime = None

    now = time.time()
    cache_hit = (
        _oauth_status_cache.get("path") == str(token_file)
        and _oauth_status_cache.get("mtime") == cur_mtime
        and (now - float(_oauth_status_cache.get("checked_at", 0.0)) < 60.0)
    )
    if cache_hit:
        ok_val = bool(_oauth_status_cache["ok"])
        return {
            "ok": ok_val,
            "label": str(_oauth_status_cache["label"]),
            "needs_reauth": not ok_val,
        }

    from lecture_pipeline.export.drive_publisher import GoogleDrivePublisher

    pub = GoogleDrivePublisher(credentials_file=cred_file, token_file=token_file, interactive=False)
    ok_val, msg = pub.check_auth_status(attempt_refresh=net_ok)

    # Re-read mtime in case check_auth_status wrote a refreshed token.json
    try:
        new_mtime = token_file.stat().st_mtime
    except Exception:
        new_mtime = cur_mtime

    _oauth_status_cache.update({
        "path": str(token_file),
        "mtime": new_mtime,
        "checked_at": now,
        "ok": ok_val,
        "label": msg,
    })
    return {"ok": ok_val, "label": msg, "needs_reauth": not ok_val}


def trigger_google_reauth() -> Dict[str, Any]:
    """Start interactive Google OAuth re-authorization in background and flush pending uploads on success."""
    base_dir = CONFIG_FILE_PATH.parent
    cred_file = (base_dir / "credentials.json").resolve()
    token_p = (base_dir / "token.json").resolve()
    if not cred_file.is_file():
        return {"status": "error", "message": "找不到 credentials.json 用戶端憑證"}

    def _run_reauth():
        try:
            from lecture_pipeline.export.drive_publisher import GoogleDrivePublisher
            from lecture_pipeline.automation.offline_queue import OfflineUploadQueue

            pub = GoogleDrivePublisher(
                credentials_file=cred_file,
                token_file=token_p,
                interactive=True,
                auth_timeout_sec=1800,
            )
            # Force fresh interactive login if token is missing or invalid
            ok, _ = pub.check_auth_status(attempt_refresh=True)
            if not ok and token_p.is_file():
                try:
                    token_p.unlink()
                except Exception:
                    pass
            pub.get_service(interactive=True, timeout_seconds=1800)
            _oauth_status_cache["checked_at"] = 0.0
            broadcaster.broadcast_sync({"type": "toast", "message": "Google Drive 重新授權成功，正在檢查待補傳課堂..."})
            oq = OfflineUploadQueue()
            if oq.get_pending():
                oq.process_pending_uploads(pub)
        except Exception as e:
            _oauth_status_cache["checked_at"] = 0.0
            broadcaster.broadcast_sync({"type": "toast", "message": f"Google Drive 授權未完成：{e}"})

    threading.Thread(target=_run_reauth, daemon=True).start()
    return {"status": "ok", "message": "已於瀏覽器開啟 Google 帳號授權頁面"}


_ffmpeg_install_state: Dict[str, Any] = {"running": False, "message": "", "progress": 0}


def setup_gemini_api_key_action(api_key: str, verify: bool = True) -> Dict[str, Any]:
    """Validate and save GEMINI_API_KEY into .env and process environment."""
    from lecture_pipeline.utils.bootstrap import save_gemini_api_key
    ok, msg = save_gemini_api_key(api_key=api_key, verify=verify)
    return {"status": "ok" if ok else "error", "ok": ok, "message": msg}


def setup_credentials_action(raw_bytes: bytes) -> Dict[str, Any]:
    """Validate and save Google Cloud OAuth credentials.json."""
    from lecture_pipeline.utils.bootstrap import save_google_credentials_json
    ok, msg = save_google_credentials_json(raw_bytes)
    if ok:
        _oauth_status_cache["checked_at"] = 0.0
    return {"status": "ok" if ok else "error", "ok": ok, "message": msg}


def install_ffmpeg_action() -> Dict[str, Any]:
    """Trigger background download and installation of portable FFmpeg/FFprobe binaries."""
    from lecture_pipeline.utils.bootstrap import check_ffmpeg_ready, install_ffmpeg_locally

    already_ok, msg, path = check_ffmpeg_ready()
    if already_ok:
        return {"status": "ok", "ok": True, "message": msg, "running": False}

    if _ffmpeg_install_state["running"]:
        return {"status": "ok", "ok": True, "message": "FFmpeg 正在下載安裝中...", "running": True}

    def _run():
        _ffmpeg_install_state.update({"running": True, "message": "準備下載 FFmpeg...", "progress": 5})
        def _cb(m: str, pct: int):
            _ffmpeg_install_state.update({"message": m, "progress": pct})
            broadcaster.broadcast_sync({"type": "toast", "message": f"FFmpeg 安裝 ({pct}%): {m}"})

        try:
            ok_dl, res_msg = install_ffmpeg_locally(progress_cb=_cb)
            _ffmpeg_install_state.update({"running": False, "message": res_msg, "progress": 100 if ok_dl else 0})
            broadcaster.broadcast_sync({"type": "toast", "message": res_msg})
        except Exception as e:
            _ffmpeg_install_state.update({"running": False, "message": str(e), "progress": 0})
            broadcaster.broadcast_sync({"type": "toast", "message": f"FFmpeg 安裝失敗：{e}"})

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "ok", "ok": True, "message": "已開始自動下載安裝 FFmpeg...", "running": True}


def repair_environment_action() -> Dict[str, Any]:
    """Run environment config, folder, and corrupted state self-repair."""
    from lecture_pipeline.utils.bootstrap import repair_and_init_environment
    res = repair_and_init_environment(config_path=CONFIG_FILE_PATH)
    _oauth_status_cache["checked_at"] = 0.0
    repairs = res.get("repairs", [])
    summary = "環境與設定正常，無須修復項目。" if not repairs else "修復完成：" + "；".join(repairs)
    return {"status": "ok", "message": summary, "repairs": repairs, "paths": res.get("paths", {})}


def toggle_autostart_action(enabled: Optional[bool] = None) -> Dict[str, Any]:
    """Enable, disable, or toggle real Windows Startup registration."""
    from lecture_pipeline.automation.autostart import disable_autostart, enable_autostart, get_autostart_status
    cur = get_autostart_status()
    target = (not cur["enabled"]) if enabled is None else bool(enabled)
    try:
        if target:
            p = enable_autostart()
            return {"status": "ok", "enabled": True, "path": str(p), "message": "已啟用 Windows 開機自動啟動背景監控。"}
        disable_autostart()
        return {"status": "ok", "enabled": False, "path": cur["path"], "message": "已關閉 Windows 開機自動啟動。"}
    except Exception as e:
        return {"status": "error", "enabled": cur["enabled"], "message": f"設定開機啟動失敗：{e}"}


def get_full_system_status() -> Dict[str, Any]:
    """Collect real hardware, network, FFmpeg, token, autostart, and watcher diagnostics."""
    from lecture_pipeline.automation.autostart import get_autostart_status
    from lecture_pipeline.automation.offline_queue import OfflineUploadQueue
    from lecture_pipeline.utils.bootstrap import (
        check_ffmpeg_ready,
        check_local_stt_ready,
        get_os_summary,
        is_valid_api_key_value,
        is_webview2_available,
    )

    # 1. GPU Check
    gpu_name = "未偵測到 GPU"
    vram_free = "0 MB"
    gpu_driver = "未知"
    gpu_ok = False
    try:
        smi = silent_run(
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

    # 3. Google Docs OAuth status (validates refreshability instead of naive file-exists check)
    docs_status = _get_cached_google_auth_status(net_ok)

    # 4. Disk space
    cfg = get_current_config()
    paths = cfg.get("paths", {})
    inbox_dir = paths.get("inbox_dir", "inbox")
    disk_ok, disk_free_gb = check_disk_space(inbox_dir, min_free_gb=2.0)

    # 5. Gemini API Key check (filters out template placeholders)
    raw_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    gemini_key_set = is_valid_api_key_value(raw_key)

    # 6. FFmpeg, WebView2, Local STT, Autostart, Queue
    ffmpeg_ok, ffmpeg_label, ffmpeg_path = check_ffmpeg_ready()
    local_stt_ok, local_stt_label = check_local_stt_ready()
    webview2_ok = is_webview2_available()
    as_info = get_autostart_status()
    try:
        pending_cnt = len(OfflineUploadQueue().get_pending())
    except Exception:
        pending_cnt = 0

    has_creds_file = (CONFIG_FILE_PATH.parent / "credentials.json").is_file()
    needs_onboarding = (not gemini_key_set) or (not ffmpeg_ok)

    return {
        "mode": get_remembered_gui_mode(),
        "os_info": {
            "ok": True,
            "label": get_os_summary(),
        },
        "gpu": {
            "ok": gpu_ok,
            "name": gpu_name,
            "vram_free": vram_free,
            "driver": gpu_driver,
            "label": f"{gpu_name} ({vram_free})" if gpu_ok else "本機 CPU 運算",
        },
        "network": {
            "ok": net_ok,
            "label": "連線正常" if net_ok else "離線模式 (佇列暫存)",
        },
        "ffmpeg": {
            "ok": ffmpeg_ok,
            "label": ffmpeg_label,
            "path": ffmpeg_path,
            "installing": _ffmpeg_install_state["running"],
        },
        "webview2": {
            "ok": webview2_ok,
            "label": "Microsoft Edge WebView2 已安裝" if webview2_ok else "未安裝 WebView2（已自動改用瀏覽器模式）",
        },
        "local_stt": {
            "ok": local_stt_ok,
            "label": local_stt_label,
        },
        "google_docs": {
            **docs_status,
            "has_credentials": has_creds_file,
        },
        "api_key": {
            "ok": gemini_key_set,
            "label": "Gemini API 金鑰已設定" if gemini_key_set else "尚未設定 GEMINI_API_KEY（必要）",
        },
        "disk": {
            "ok": disk_ok,
            "free_gb": disk_free_gb,
        },
        "autostart": {
            "ok": True,
            "enabled": bool(as_info.get("enabled")),
            "label": "已啟用開機自動啟動" if as_info.get("enabled") else "尚未啟用開機啟動",
        },
        "pending_uploads": {
            "count": pending_cnt,
            "label": f"待補傳佇列共 {pending_cnt} 筆" if pending_cnt else "待處理佇列共 0 筆",
        },
        "needs_onboarding": needs_onboarding,
        "default_paths": {
            "record_archive_dir": str(Path.home() / "Documents" / "課堂錄音" / "錄音封存"),
        },
        "watcher": {
            "paused": is_watcher_paused(),
            "badge": "已暫停" if is_watcher_paused() else "待命",
        },
        "active_task": get_current_task_status(),
    }


# ---------------------------------------------------------------------------
# History & Lecture Library
# ---------------------------------------------------------------------------

_lecture_cache: Dict[str, Path] = {}
_drive_log_cache: Dict[str, Any] = {"path": "", "mtime": -1.0, "entries": []}


def _strip_bundle_header(text: str) -> str:
    """Strip NotebookLM export header boilerplate so character counts reflect actual lecture content."""
    if not text:
        return ""
    if text.startswith("# 【") and "\n> **課程名稱" in text[:600]:
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return text.strip()


def _extract_drive_links_from_run_dict(rdata: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Extract (docs_url, drive_folder_url) from a parsed run.json dictionary."""
    if not isinstance(rdata, dict):
        return None, None
    meta = rdata.get("metadata") if isinstance(rdata.get("metadata"), dict) else {}
    folder_link = rdata.get("drive_link") or meta.get("drive_course_folder_link")
    summary_link = rdata.get("docs_url") or meta.get("drive_summary_doc_link")
    docs_url = summary_link or folder_link
    return (str(docs_url) if docs_url else None), (str(folder_link) if folder_link else None)


def _index_work_runs_by_export_dir(work_dir: Path) -> Dict[str, Path]:
    """Map resolved export_mirrored_dir -> newest matching work run.json path."""
    mapping: Dict[str, tuple[float, Path]] = {}
    if not work_dir.is_dir():
        return {}
    try:
        for rj in work_dir.rglob("run.json"):
            if not rj.is_file():
                continue
            try:
                rdata = json.loads(rj.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta = rdata.get("metadata") if isinstance(rdata.get("metadata"), dict) else {}
            exp_mirrored = meta.get("export_mirrored_dir")
            if not exp_mirrored:
                continue
            try:
                key = str(Path(exp_mirrored).resolve()).lower()
                mt = rj.stat().st_mtime
            except OSError:
                continue
            if key not in mapping or mt > mapping[key][0]:
                mapping[key] = (mt, rj)
    except Exception:
        pass
    return {k: v[1] for k, v in mapping.items()}


def _find_matching_work_run_json(
    export_item_dir: Path,
    course_name: str,
    date_str: str,
    work_dir: Path,
    export_dir_index: Optional[Dict[str, Path]] = None,
) -> Optional[Path]:
    """Locate the work/<course>/<date>/run.json corresponding to a specific exported lecture directory."""
    if not work_dir.is_dir():
        return None

    clean_d = date_str.replace("-", "") if date_str else ""
    date_candidates = [d for d in (export_item_dir.name, clean_d, f"20{clean_d}" if len(clean_d) == 6 else "") if d]
    course_candidates = []
    for c in (course_name, export_item_dir.parent.name):
        if c and c not in course_candidates:
            course_candidates.append(c)

    for c in course_candidates:
        for dv in date_candidates:
            cand = work_dir / c / dv / "run.json"
            if cand.is_file():
                return cand

    if export_dir_index is None:
        export_dir_index = _index_work_runs_by_export_dir(work_dir)
    try:
        resolved_key = str(export_item_dir.resolve()).lower()
        if resolved_key in export_dir_index:
            return export_dir_index[resolved_key]
    except OSError:
        pass
    return None


def _get_drive_link_from_logs(course_or_id: str, date_str: str = "") -> Optional[str]:
    """Extract course+date-matched Google Drive/Docs link from watcher.log without cross-lecture leaks."""
    if not course_or_id:
        return None

    log_file = _resolve_app_path("logs/watcher.log")
    if not log_file.is_file():
        return None

    try:
        stat = log_file.stat()
        mtime = stat.st_mtime
    except OSError:
        return None

    log_key = str(log_file)
    if _drive_log_cache["path"] != log_key or _drive_log_cache["mtime"] != mtime:
        entries: List[tuple[str, str, str, str]] = []
        try:
            content = log_file.read_text(encoding="utf-8", errors="ignore")
            url_pat = re.compile(r"(https://(?:drive|docs)\.google\.com/[^\s\r\n)\]]+)")
            tag_pat = re.compile(r"\[([^\|\[\]]+?)\s*\|\s*([0-9_]+)\]")
            for line in content.splitlines():
                if "已同步至 Google Drive" not in line:
                    continue
                um = url_pat.search(line)
                if not um:
                    continue
                url = um.group(1)
                tm = tag_pat.search(line)
                if tm:
                    entries.append((tm.group(1).strip(), tm.group(2).strip(), line, url))
                else:
                    entries.append(("", "", line, url))
        except Exception:
            entries = []
        _drive_log_cache["path"] = log_key
        _drive_log_cache["mtime"] = mtime
        _drive_log_cache["entries"] = entries

    clean_d = date_str.replace("-", "") if date_str else ""
    for entry_course, entry_date, raw_line, url in reversed(_drive_log_cache["entries"]):
        if entry_course:
            if entry_course == course_or_id:
                if not clean_d or entry_date.startswith(clean_d) or clean_d in entry_date:
                    return url
        else:
            if course_or_id in raw_line and (not clean_d or clean_d in raw_line):
                return url
    return None


def resolve_lecture_audio_path(
    target_dir: Path,
    course_name: Optional[str] = None,
    date_str: Optional[str] = None,
) -> Optional[Path]:
    """Precisely resolve the audio file associated with a lecture.

    Prevents cross-lecture audio contamination by strictly matching both course
    and date, prioritizing long-term archives (Record) and original archives before work directory.
    """
    # 1. Direct audio inside target_dir or recorded pointer
    meta_json = target_dir / "audio_source.json"
    if meta_json.is_file():
        try:
            with open(meta_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            p_str = data.get("audio_path")
            if p_str and Path(p_str).is_file():
                return Path(p_str).resolve()
        except Exception:
            pass

    for ext in [".opus", ".m4a", ".mp3", ".wav", ".flac"]:
        matched = [f for f in target_dir.glob(f"*{ext}") if not f.name.startswith("chunk")]
        if matched:
            matched.sort(key=lambda f: (not f.name.endswith(".opus"), not f.name.endswith(".m4a"), f.name.startswith("00_normalized")))
            return matched[0].resolve()

    cfg = get_current_config()
    paths = cfg.get("paths", {})
    rec_archive_str = paths.get("record_archive_dir")
    rec_archive = _resolve_app_path(rec_archive_str) if rec_archive_str else None
    archive_dir = _resolve_app_path(paths.get("archive_dir", "archive"))
    work_dir = _resolve_app_path(paths.get("work_dir", "work"))

    if course_name:
        m_course = re.match(r"^(\d{6,8})_(.+)$", course_name)
        if m_course:
            course_name = m_course.group(2)
            if not date_str:
                date_str = m_course.group(1)

    if not date_str:
        dm = re.search(r"(20\d{6}|\d{6})", target_dir.name)
        if dm:
            date_str = dm.group(1)

    clean_d = date_str.replace("-", "") if date_str else ""
    date_variants: List[str] = []
    if clean_d:
        date_variants.append(clean_d)
        if len(clean_d) == 8:
            date_variants.append(clean_d[2:])
        elif len(clean_d) == 6:
            date_variants.append(f"20{clean_d}")
    if date_str and date_str not in date_variants:
        date_variants.append(date_str)

    def _find_audio_in_dir(d: Path) -> Optional[Path]:
        if not d.is_dir():
            return None
        audio_files = []
        for ext in [".opus", ".m4a", ".mp3", ".wav", ".flac"]:
            for f in d.glob(f"*{ext}"):
                if f.is_file() and not f.name.startswith("chunk"):
                    audio_files.append(f)
        if audio_files:
            audio_files.sort(key=lambda f: (
                not f.name.endswith(".opus"),
                not f.name.endswith(".m4a"),
                not f.name.endswith(".mp3"),
                f.name.startswith("00_normalized"),
            ))
            return audio_files[0].resolve()
        return None

    # 2. Check Record Long-Term Archive (highest priority for completed lectures)
    if rec_archive and rec_archive.is_dir():
        if course_name:
            for dv in date_variants:
                found = _find_audio_in_dir(rec_archive / course_name / dv)
                if found:
                    return found
            found = _find_audio_in_dir(rec_archive / course_name)
            if found:
                return found

    # 3. Check Original Archive (inbox archive)
    if archive_dir and archive_dir.is_dir():
        if course_name:
            for dv in date_variants:
                found = _find_audio_in_dir(archive_dir / course_name / dv)
                if found:
                    return found
            found = _find_audio_in_dir(archive_dir / course_name)
            if found:
                return found

    # 4. Check Work Directory ONLY if course_name and date_str match precisely
    # Never fall back to global rglob which grabs whatever is currently in work/
    if work_dir and work_dir.is_dir() and course_name:
        for dv in date_variants:
            found = _find_audio_in_dir(work_dir / course_name / dv)
            if found:
                return found

    return None



def get_archived_lectures() -> List[Dict[str, Any]]:
    """Recursively scan exports directory for processed lecture note bundles.
    
    Specification: 課堂與播放庫顯示的檔案是筆記匯出目錄 (export_dir)，
    嚴格禁止顯示長期封存庫 (archive_dir, record_archive_dir)。
    """
    global _lecture_cache
    cfg = get_current_config()
    paths = cfg.get("paths", {})
    work_dir = _resolve_app_path(paths.get("work_dir", "work"))
    export_dir = _resolve_app_path(cfg.get("export", {}).get("export_dir", "exports"))
    work_export_index = _index_work_runs_by_export_dir(work_dir)

    # 課堂與播放庫顯示的檔案是筆記匯出目錄，禁止顯示長期封存庫
    search_dirs = [export_dir]

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
            drive_folder_url = None
            if run_json.is_file():
                try:
                    with open(run_json, "r", encoding="utf-8") as rf:
                        rdata = json.load(rf)
                        course_name = rdata.get("course", "")
                        raw_date = str(rdata.get("date", ""))
                        if len(raw_date) == 8:
                            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                        if rdata.get("channel"):
                            channel_name = str(rdata["channel"])
                        docs_url, drive_folder_url = _extract_drive_links_from_run_dict(rdata)
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
                work_run_json = _find_matching_work_run_json(
                    item, course_name, date_str, work_dir, work_export_index
                )
                if work_run_json and work_run_json.is_file():
                    try:
                        wr_data = json.loads(work_run_json.read_text(encoding="utf-8"))
                        docs_url, w_folder = _extract_drive_links_from_run_dict(wr_data)
                        drive_folder_url = drive_folder_url or w_folder
                        w_meta = wr_data.get("metadata") if isinstance(wr_data.get("metadata"), dict) else {}
                        if w_meta.get("stt_channel"):
                            channel_name = str(w_meta["stt_channel"])
                    except Exception:
                        pass

            if not docs_url:
                docs_url = _get_drive_link_from_logs(course_name, date_str)
                if docs_url and not drive_folder_url and "/folders/" in docs_url:
                    drive_folder_url = docs_url

            audio_path = resolve_lecture_audio_path(item, course_name, date_str)

            summary_text = summary_file.read_text(encoding="utf-8") if summary_file.is_file() else ""
            cleaned_text = cleaned_file.read_text(encoding="utf-8") if cleaned_file.is_file() else ""
            raw_text = raw_ts_file.read_text(encoding="utf-8") if raw_ts_file.is_file() else (raw_file.read_text(encoding="utf-8") if raw_file.is_file() else "")
            cleaned_body = _strip_bundle_header(cleaned_text)
            raw_body = _strip_bundle_header(raw_text)

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
                "has_raw": bool(raw_body),
                "has_audio": audio_path is not None,
                "audio_filename": audio_path.name if audio_path else None,
                "audio_path": str(audio_path.resolve()) if audio_path else None,
                "audio_url": f"/api/audio/{lecture_id_clean}" if audio_path else None,
                "summary_preview": summary_text[:200] if summary_text else "",
                "char_count": len(cleaned_body) or len(raw_body),
                "channel": channel_name,
                "docs_url": docs_url,
                "drive_folder_url": drive_folder_url,
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

    cfg = get_current_config()
    paths = cfg.get("paths", {})
    work_dir = _resolve_app_path(paths.get("work_dir", "work"))
    export_dir = _resolve_app_path(cfg.get("export", {}).get("export_dir", "exports"))

    if not target_dir or not target_dir.is_dir():
        for p in export_dir.rglob(f"*{lecture_id}*"):
            if p.is_dir():
                target_dir = p
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

    run_json = target_dir / "run.json"
    course_name = ""
    date_str = ""
    docs_url = None
    drive_folder_url = None
    if run_json.is_file():
        try:
            with open(run_json, "r", encoding="utf-8") as rf:
                rdata = json.load(rf)
                course_name = rdata.get("course", "")
                date_str = str(rdata.get("date", ""))
                docs_url, drive_folder_url = _extract_drive_links_from_run_dict(rdata)
        except Exception:
            pass

    if not course_name:
        if target_dir.parent.name and target_dir.parent.name not in ["work", "archive", "exports", "Record", export_dir.name]:
            course_name = target_dir.parent.name
        else:
            m = re.match(r"^(\d{6,8})_(.+)$", target_dir.name)
            if m:
                course_name = m.group(2)
            else:
                course_name = target_dir.name

    if not date_str:
        date_match = re.search(r"(20\d{6}|\d{6})", target_dir.name)
        date_str = date_match.group(1) if date_match else ""

    if not docs_url:
        work_run_json = _find_matching_work_run_json(target_dir, course_name, date_str, work_dir)
        if work_run_json and work_run_json.is_file():
            try:
                wr_data = json.loads(work_run_json.read_text(encoding="utf-8"))
                docs_url, w_folder = _extract_drive_links_from_run_dict(wr_data)
                drive_folder_url = drive_folder_url or w_folder
            except Exception:
                pass

    if not docs_url:
        docs_url = _get_drive_link_from_logs(course_name, date_str)
        if docs_url and not drive_folder_url and "/folders/" in docs_url:
            drive_folder_url = docs_url

    audio_file = resolve_lecture_audio_path(target_dir, course_name, date_str)

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
        "drive_folder_url": drive_folder_url,
    }


def resolve_export_lecture_file(lecture_id: str, file_type: str = "summary") -> Optional[Path]:
    """Resolve target note file directed to the Exports directory (筆記與播放器匯出目錄)."""
    cfg = get_current_config()
    export_dir = _resolve_app_path(cfg.get("export", {}).get("export_dir", "exports"))
    export_dir.mkdir(parents=True, exist_ok=True)

    detail = get_lecture_content(lecture_id)
    course = (detail.get("course") if detail else "") or ""
    title = (detail.get("title") if detail else "") or ""
    date_match = re.search(r"(20\d{6}|\d{6})", f"{lecture_id}_{title}")
    date_str = date_match.group(1) if date_match else ""

    if not course:
        m = re.match(r"^(\d{6,8})_(.+)$", lecture_id)
        if m:
            course = m.group(2)
        else:
            m2 = re.match(r"^(.+)_(\d{6,8})$", lecture_id)
            if m2:
                course = m2.group(1)
            else:
                course = lecture_id

    if file_type == "summary":
        patterns = ["*重點總結筆記.md", "*重點筆記*.md", "*筆記*.md", "01_*.md", "03_summary.md", "*.md"]
    elif file_type == "cleaned":
        patterns = ["*完整課堂整理文稿.md", "*整理文稿*.md", "02_*.md", "02_cleaned.txt", "*.txt"]
    elif file_type == "raw":
        patterns = ["*原始時間戳對照.md", "*時間戳*.md", "03_*.md", "01_raw_timestamped.txt", "01_raw.txt", "*.txt"]
    else:
        patterns = ["*.md", "*.txt"]

    # 0. Exact directory already known from detail["path"] (prevents cross-date collision)
    if detail and detail.get("path"):
        d_path = Path(detail["path"])
        if d_path.is_dir():
            for pat in patterns:
                for sf in d_path.glob(pat):
                    if sf.is_file():
                        return sf

    # 1. Look in export_dir / course / date_str
    if course and date_str:
        c_dir = export_dir / course / date_str
        if c_dir.is_dir():
            for pat in patterns:
                for f in c_dir.glob(pat):
                    if f.is_file():
                        return f

    # 2. Look in export_dir / course matching date_str when present
    if course:
        c_dir = export_dir / course
        if c_dir.is_dir():
            for pat in patterns:
                for f in c_dir.rglob(pat):
                    if f.is_file() and (not date_str or date_str in f.name or date_str in str(f.parent)):
                        return f

    # 3. Look across export_dir requiring date_str match whenever date_str is known
    for pat in patterns:
        for f in export_dir.rglob(pat):
            if not f.is_file():
                continue
            if date_str and date_str not in str(f):
                continue
            if course and course.lower() in str(f).lower():
                return f
            if lecture_id and lecture_id.lower() in str(f).lower():
                return f

    # 5. Copy or create note in export_dir / course / date_str
    c_name = course or "Lecture"
    d_name = date_str or time.strftime("%Y%m%d")
    target_exp_dir = export_dir / c_name / d_name
    target_exp_dir.mkdir(parents=True, exist_ok=True)

    dest_file = target_exp_dir / f"01_{c_name}_{d_name}_重點總結筆記.md"
    summary_text = (detail.get("summary") if detail else "") or ""
    if not summary_text:
        paths = cfg.get("paths", {})
        for base_dir in [_resolve_app_path(paths.get("work_dir", "work")), _resolve_app_path(paths.get("archive_dir", "archive"))]:
            if not base_dir.is_dir():
                continue
            for p in base_dir.rglob(f"*{lecture_id}*"):
                if p.is_dir():
                    s_file = p / "03_summary.md"
                    if s_file.is_file():
                        summary_text = s_file.read_text(encoding="utf-8")
                        break
            if summary_text:
                break

    if not summary_text:
        summary_text = f"# 【課堂重點筆記】{c_name} ({d_name})\n\n（尚未產生筆記或處理中）"

    dest_file.write_text(summary_text, encoding="utf-8")
    return dest_file


# ---------------------------------------------------------------------------
# Native Windows Desktop Integration
# ---------------------------------------------------------------------------

def open_folder_in_desktop(folder_path: Path | str) -> bool:
    """Open target folder in Windows File Explorer or create if not existing."""
    p = _resolve_app_path(folder_path)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return True
    try:
        if sys.platform == "win32":
            env = _clean_desktop_env()
            cmd = ["explorer.exe", f"/select,{str(p)}"] if p.is_file() else ["explorer.exe", str(p)]
            try:
                subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    env=env,
                )
                return True
            except OSError:
                os.startfile(str(p.parent if p.is_file() else p))
                return True
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
            return True
        else:
            subprocess.Popen(["xdg-open", str(p)])
            return True
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to open folder {p}: {e}")
        return False


def open_file_in_desktop(file_path: Path | str) -> bool:
    """Open target file using system default application, with Notepad fallback."""
    p = _resolve_app_path(file_path)
    if not p.is_file():
        return False
    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return True
    try:
        if sys.platform == "win32":
            env = _clean_desktop_env()
            try:
                os.startfile(str(p))
                return True
            except OSError:
                subprocess.Popen(
                    ["notepad.exe", str(p)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    env=env,
                )
                return True
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
            return True
        else:
            subprocess.Popen(["xdg-open", str(p)])
            return True
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to open file {p}: {e}")
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    ["notepad.exe", str(p)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    env=_clean_desktop_env(),
                )
                return True
        except Exception:
            pass
        return False


def open_file_in_vscode_preview(file_path: Path | str) -> bool:
    """Open a Markdown note file in VS Code (direct Code.exe binary), falling back to desktop open."""
    p = _resolve_app_path(file_path)
    if not p.is_file():
        return False
    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return True

    code_exe = _resolve_vscode_executable()
    if not code_exe:
        return open_file_in_desktop(p)

    try:
        cmd = [code_exe, "-r", str(p)]
        env = _clean_desktop_env()
        if sys.platform == "win32":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            subprocess.Popen(
                cmd,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
                env=env,
            )
        else:
            subprocess.Popen(
                cmd,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env=env,
            )
        return True
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").warning(f"Failed to open in VS Code: {e}")
        return open_file_in_desktop(p)


def browse_folder_dialog(initial_dir: Optional[str] = None) -> Optional[str]:
    """Launch native Windows directory selection dialog."""
    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return str(_resolve_app_path(initial_dir or "."))
    init_path: Optional[Path] = None
    if initial_dir:
        cand = _resolve_app_path(initial_dir)
        if cand.is_dir():
            init_path = cand

    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            initialdir=str(init_path or _get_app_base_dir()),
            title="選擇資料夾路徑",
        )
        root.destroy()
        return selected if selected else None
    except Exception:
        pass

    try:
        env = _clean_desktop_env()
        if init_path is not None:
            env["LP_BROWSE_INIT_DIR"] = str(init_path)
        ps_cmd = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            "$owner = New-Object System.Windows.Forms.Form;"
            "$owner.TopMost = $true; $owner.StartPosition = 'Manual';"
            "$owner.Location = New-Object System.Drawing.Point(-32000,-32000);"
            "$owner.Size = New-Object System.Drawing.Size(1,1); $owner.ShowInTaskbar = $false;"
            "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$dlg.Description = '請選擇資料夾路徑';"
            "if($env:LP_BROWSE_INIT_DIR -and (Test-Path $env:LP_BROWSE_INIT_DIR)){$dlg.SelectedPath = $env:LP_BROWSE_INIT_DIR};"
            "try { if($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Out.Write($dlg.SelectedPath)} }"
            "finally { $dlg.Dispose(); $owner.Dispose() }"
        )
        res = silent_run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").warning(f"browse_folder_dialog failed: {e}")
    return None


def browse_file_dialog(initial_dir: Optional[str] = None, file_types: Optional[List[tuple[str, str]]] = None) -> Optional[str]:
    """Launch native Windows file selection dialog."""
    if os.environ.get("LECTURE_PIPELINE_TEST_MODE") == "1":
        return str(_resolve_app_path(initial_dir or "."))
    init_path: Optional[Path] = None
    if initial_dir:
        cand = _resolve_app_path(initial_dir)
        if cand.is_dir():
            init_path = cand

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
            initialdir=str(init_path or _get_app_base_dir()),
            title="請選擇檔案",
            filetypes=types,
        )
        root.destroy()
        return selected if selected else None
    except Exception:
        pass

    try:
        env = _clean_desktop_env()
        if init_path is not None:
            env["LP_BROWSE_INIT_DIR"] = str(init_path)
        ps_cmd = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            "$owner = New-Object System.Windows.Forms.Form;"
            "$owner.TopMost = $true; $owner.StartPosition = 'Manual';"
            "$owner.Location = New-Object System.Drawing.Point(-32000,-32000);"
            "$owner.Size = New-Object System.Drawing.Size(1,1); $owner.ShowInTaskbar = $false;"
            "$dlg = New-Object System.Windows.Forms.OpenFileDialog;"
            "$dlg.Filter = '所有支援檔案 (*.pdf;*.txt;*.md;*.wav;*.mp3;*.m4a)|*.pdf;*.txt;*.md;*.wav;*.mp3;*.m4a|所有檔案 (*.*)|*.*';"
            "$dlg.Title = '請選擇檔案';"
            "if($env:LP_BROWSE_INIT_DIR -and (Test-Path $env:LP_BROWSE_INIT_DIR)){$dlg.InitialDirectory = $env:LP_BROWSE_INIT_DIR};"
            "try { if($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Out.Write($dlg.FileName)} }"
            "finally { $dlg.Dispose(); $owner.Dispose() }"
        )
        res = silent_run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").warning(f"browse_file_dialog failed: {e}")
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
        "detail": "任務已中止，已處理的進度保留於暫存目錄。",
    })
    return {"status": "ok", "message": "任務已中止"}


def get_task_abort_event() -> threading.Event:
    return _task_abort_event


def get_task_pause_event() -> threading.Event:
    return _task_pause_event


_last_progress_sig: Optional[str] = None
_last_progress_time: float = 0.0
_progress_debounce_lock = threading.Lock()


def update_task_progress(stage: str, progress: int, detail: str, task_name: str = "", running: bool = True) -> None:
    """Update active task state and broadcast to all connected WebSocket GUI clients and tray."""
    global _current_task_info, _last_progress_sig, _last_progress_time
    now = time.time()
    sig = f"{stage}:{progress}:{detail}:{task_name}:{running}"
    with _progress_debounce_lock:
        if sig == _last_progress_sig and (now - _last_progress_time) < 1.0:
            return
        _last_progress_sig = sig
        _last_progress_time = now

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
            "stage": "待命",
            "progress": 100,
            "detail": detail or f"課堂已歸檔至 {lecture_id}",
        })

    if _tray_instance:
        _tray_instance.increment_processed()
        _tray_instance.update_status(state="idle", detail="監看 Inbox", progress="等待音檔")

    broadcaster.broadcast_sync({
        "type": "progress",
        "running": False,
        "progress": 100,
        "stage": "處理完成",
        "detail": detail or f"課堂已歸檔至 {lecture_id}",
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
            "detail": f"處理失敗：{error_msg}",
        })

    if _tray_instance:
        _tray_instance.update_status(state="idle", detail="處理失敗", progress="等待音檔")

    broadcaster.broadcast_sync({
        "type": "progress",
        "running": False,
        "progress": 0,
        "stage": "處理失敗",
        "detail": f"處理失敗：{error_msg}",
    })


def start_processing_task(audio_files: List[Path], channel_id: str = "C1") -> bool:
    """Launch end-to-end lecture pipeline in background thread with WebSocket progress broadcasts."""
    global _current_task_info
    with _processing_lock:
        if _current_task_info.get("running", False):
            return False

        task_token = f"gui_task_{audio_files[0].name if audio_files else 'lecture'}"
        from lecture_pipeline.automation.watcher import acquire_processing_files
        if not acquire_processing_files(audio_files, token=task_token):
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
            "stage": "準備處理",
            "progress": 5,
            "detail": f"開始處理音訊檔案：{', '.join(file_names)}",
            "start_time": time.time(),
        }

    def _worker():
        global _current_task_info
        try:
            cfg = get_current_config()
            paths = cfg.get("paths", {})
            archive_dir = paths.get("archive_dir", "archive")
            rec_archive = paths.get("record_archive_dir")
            export_dir = cfg.get("export", {}).get("export_dir", "exports")

            pre_cfg = cfg.get("preprocessing", {})
            if isinstance(pre_cfg, dict):
                pre_flags_list = [f"{k}={v}" for k, v in pre_cfg.items()]
                pre_flags_val = ",".join(pre_flags_list) if pre_flags_list else "hp=100Hz,loud=-19LUFS"
            else:
                pre_flags_val = str(pre_cfg) or "hp=100Hz,loud=-19LUFS"

            out_dir = run_pipeline_for_group(
                audio_files=audio_files,
                channel_id=channel_id,
                pre_flags=pre_flags_val,
                cleanup_model=cfg.get("cleanup", {}).get("model", "gemini-3.5-flash-lite"),
                summary_model=cfg.get("summarize", {}).get("model", "gemini-3.5-flash-lite"),
                archive_dir=archive_dir,
                upload_drive=True,
                tray=_tray_instance,
                record_archive_base_dir=rec_archive,
                abort_event=_task_abort_event,
                export_dir=export_dir,
                lock_token=task_token,
            )

        except InterruptedError as ie:
            if _tray_instance:
                _tray_instance.update_status(state="idle", detail="任務已中止", progress="等待音檔")
            broadcaster.broadcast_sync({
                "type": "progress",
                "running": False,
                "progress": 0,
                "stage": "已中止",
                "detail": "任務已中止，已處理的進度保留於暫存目錄。",
            })
            broadcaster.broadcast_sync({
                "type": "task_aborted",
                "detail": str(ie),
            })
        except Exception as e:
            from lecture_pipeline.automation.quota import (
                QuotaManager,
                is_api_or_quota_error,
                is_quota_exhausted_error,
                is_temporary_service_error,
            )
            if is_api_or_quota_error(e):
                qm = QuotaManager()
                next_reset = qm.calculate_next_reset_datetime()
                reset_str = next_reset.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                if is_temporary_service_error(e):
                    msg = f"服務持續繁忙 (503 重試 3 次未果)，進度已保留，已延遲至重置時間重試（{reset_str}）"
                    notif_title = "伺服器暫時繁忙，已延遲重試"
                else:
                    msg = f"API 配額已用盡 (429)，進度已保留，已延遲至重置時間重試（{reset_str}）"
                    notif_title = "API 配額用盡，已延遲重試"

                with _processing_lock:
                    _current_task_info.update({
                        "running": False,
                        "stage": "API 延遲重試",
                        "progress": 0,
                        "detail": msg,
                    })
                broadcaster.broadcast_sync({
                    "type": "progress",
                    "running": False,
                    "progress": 0,
                    "stage": "API 延遲重試",
                    "detail": msg,
                })
                send_notification(
                    title=notif_title,
                    message=f"{primary_name} 進度已保留，將延遲至重置時間重試",
                )
            else:
                notify_task_error(error_msg=str(e), task_name=primary_name)
        finally:
            from lecture_pipeline.automation.watcher import release_processing_files
            release_processing_files(audio_files, token=task_token)
            with _processing_lock:
                _current_task_info = {"running": False, "paused": False, "aborted": False}

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    return True


# ---------------------------------------------------------------------------
# Glossary Management & PDF Extraction
# ---------------------------------------------------------------------------

def get_courses_base_dir() -> Path:
    """Retrieve absolute path to courses directory based on config or project root."""
    try:
        cfg = get_current_config()
        paths = cfg.get("paths", {})
        raw_dir = paths.get("courses_dir", "courses")
    except Exception:
        raw_dir = "courses"
    p = Path(raw_dir)
    if not p.is_absolute():
        if getattr(sys, "frozen", False):
            project_root = Path(sys.executable).resolve().parent
        else:
            project_root = Path(__file__).resolve().parents[3]
        p = (project_root / p).resolve()
    return p


def _extract_term_name(line: str) -> str:
    """Extract raw term name from a glossary entry line (ignoring comments and translations)."""
    cleaned = line.split("#", 1)[0].strip()
    if not cleaned:
        return ""
    m = re.match(r"^([^\(（\r\n]+?)\s*[\(（](.+?)[\)）]$", cleaned)
    if m:
        return m.group(1).strip()
    return cleaned


def get_course_glossary_terms(course: str = "") -> List[Dict[str, Any]]:
    """Load extracted terms for course or empty list if no course specified/available."""
    if not course or not course.strip():
        return []

    from lecture_pipeline.glossary import load_course_glossary
    courses_dir = get_courses_base_dir()
    terms = load_course_glossary(course.strip(), courses_dir=courses_dir)

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
    courses_dir = get_courses_base_dir()
    if not courses_dir.is_dir():
        return []
    courses = [p.name for p in courses_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    courses.sort()
    return courses


def extract_terms_from_uploaded_file(file_content: bytes, filename: str, course: str = "") -> List[Dict[str, Any]]:
    """Save temporary syllabus file, extract domain terms locally, and update course glossary."""
    from lecture_pipeline.glossary import extract_terms_from_file, update_course_glossary

    courses_dir = get_courses_base_dir()
    target_course = course.strip() or Path(filename).stem
    temp_dir = courses_dir / target_course
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / filename
    temp_file.write_bytes(file_content)

    try:
        extracted = extract_terms_from_file(temp_file)
        if extracted:
            update_course_glossary(target_course, extracted, courses_dir=courses_dir)
        return get_course_glossary_terms(target_course)
    except Exception as e:
        logging.getLogger("lecture_pipeline.gui").error(f"Failed to extract glossary from {filename}: {e}")
        return get_course_glossary_terms(target_course)


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
        "message": f"課堂資訊已更新",
        "lecture": get_lecture_content(lecture_id),
    }


def delete_lecture(lecture_id: str) -> Dict[str, Any]:
    """Safely delete lecture directory in exports and cache."""
    global _lecture_cache
    if not _lecture_cache:
        get_archived_lectures()

    target_dir = _lecture_cache.get(lecture_id)
    if not target_dir or not target_dir.is_dir():
        return {"status": "error", "message": f"找不到指定課堂目錄：{lecture_id}"}

    cfg = get_current_config()
    export_dir = Path(cfg.get("export", {}).get("export_dir", "exports")).resolve()
    # 安全限制：僅允許刪除筆記匯出目錄 (Exports) 中的檔案，禁止刪除封存目錄或原始資料夾
    try:
        target_dir.resolve().relative_to(export_dir)
    except ValueError:
        return {
            "status": "error",
            "message": "安全防護限制：僅允許刪除筆記匯出目錄 (Exports) 中的檔案，禁止刪除封存目錄或原始錄音資料夾。",
        }

    try:
        shutil.rmtree(str(target_dir))
        _lecture_cache.pop(lecture_id, None)
        _lecture_cache.clear()
        return {"status": "ok", "message": "課堂筆記產物已安全刪除"}
    except Exception as e:
        return {"status": "error", "message": f"刪除課堂目錄失敗：{e}"}


def add_course(course: str) -> Dict[str, Any]:
    """Create a new course directory and glossary file."""
    clean_course = re.sub(r'[\/\\:\*\?"<>\|]', '_', course).strip()
    if not clean_course:
        return {"status": "error", "message": "科目名稱不可為空"}

    courses_dir = get_courses_base_dir()
    course_dir = courses_dir / clean_course
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
    courses_dir = get_courses_base_dir()
    course_dir = courses_dir / course
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
    """Add a domain term to course glossary (with deduplication)."""
    clean_term = term.strip()
    if not clean_term:
        return {"status": "error", "message": "名詞不可為空"}

    courses_dir = get_courses_base_dir()
    course_dir = courses_dir / course
    course_dir.mkdir(parents=True, exist_ok=True)
    glossary_file = course_dir / "glossary.txt"

    entry = f"{clean_term} ({translation.strip()})" if translation.strip() else clean_term

    # Avoid duplicate lines
    existing_lines = glossary_file.read_text(encoding="utf-8").splitlines() if glossary_file.is_file() else []
    target = clean_term.lower()
    for el in existing_lines:
        if _extract_term_name(el).lower() == target:
            return {
                "status": "ok",
                "course": course,
                "terms": get_course_glossary_terms(course),
                "message": f"專有名詞「{clean_term}」已存在於詞庫中",
            }

    with open(glossary_file, "a", encoding="utf-8") as f:
        f.write(f"\n{entry}\n")

    return {
        "status": "ok",
        "course": course,
        "terms": get_course_glossary_terms(course),
        "message": f"已新增專有名詞：{entry}",
    }


def edit_glossary_term(course: str, old_term: str, new_term: str, new_translation: str = "") -> Dict[str, Any]:
    """Edit an existing term in course glossary using exact term name matching."""
    courses_dir = get_courses_base_dir()
    course_dir = courses_dir / course
    glossary_file = course_dir / "glossary.txt"
    if not glossary_file.is_file():
        return {"status": "error", "message": "詞庫檔不存在"}

    lines = glossary_file.read_text(encoding="utf-8").splitlines()
    updated_lines = []
    replaced = False

    new_entry = f"{new_term.strip()} ({new_translation.strip()})" if new_translation.strip() else new_term.strip()
    old_target = old_term.strip().lower()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            updated_lines.append(line)
        elif not replaced and _extract_term_name(stripped).lower() == old_target:
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
    """Delete a domain term from course glossary using exact term name matching."""
    courses_dir = get_courses_base_dir()
    course_dir = courses_dir / course
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
        elif _extract_term_name(stripped).lower() == target:
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


# ---------------------------------------------------------------------------
# Prompt Template Catalog & Scenario Management
# ---------------------------------------------------------------------------

def get_prompt_templates_payload() -> Dict[str, Any]:
    """Return all cleanup/summary prompt templates, presets, and active selections."""
    from lecture_pipeline.prompt_catalog import list_prompt_templates

    base_dir = CONFIG_FILE_PATH.resolve().parent
    catalog = list_prompt_templates(base_dir=base_dir)
    cfg = get_current_config()

    cleanup_raw = str(cfg.get("cleanup", {}).get("prompt_template") or "videolingo_punct.md")
    summary_raw = str(cfg.get("summarize", {}).get("template") or "fabric_summarize_lecture.md")
    active_cleanup = Path(cleanup_raw).name
    active_summary = Path(summary_raw).name

    active_preset = "custom"
    for preset in catalog["presets"]:
        if preset["cleanup_template"] == active_cleanup and preset["summary_template"] == active_summary:
            active_preset = preset["id"]
            break

    return {
        "status": "ok",
        "active_cleanup": active_cleanup,
        "active_summary": active_summary,
        "active_preset": active_preset,
        "cleanup_templates": catalog["cleanup_templates"],
        "summary_templates": catalog["summary_templates"],
        "presets": catalog["presets"],
        "category_labels": catalog["category_labels"],
    }


def select_prompt_templates_action(
    cleanup_template: Optional[str] = None,
    summary_template: Optional[str] = None,
    preset_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Switch active cleanup/summary templates or apply a multi-scene preset in config.yaml."""
    from lecture_pipeline.prompt_catalog import (
        SCENARIO_PRESETS,
        _sanitize_template_id,
        resolve_prompt_file,
    )

    base_dir = CONFIG_FILE_PATH.resolve().parent
    preset_label = None
    if preset_id and preset_id != "custom":
        matched = next((p for p in SCENARIO_PRESETS if p["id"] == preset_id), None)
        if not matched:
            return {"status": "error", "message": f"找不到場景預設：{preset_id}"}
        cleanup_template = matched["cleanup_template"]
        summary_template = matched["summary_template"]
        preset_label = matched["name"]

    updates: Dict[str, Any] = {}
    if cleanup_template:
        try:
            c_id = _sanitize_template_id(cleanup_template)
        except ValueError as ve:
            return {"status": "error", "message": str(ve)}
        if not resolve_prompt_file(c_id, base_dir=base_dir).is_file():
            return {"status": "error", "message": f"找不到整理提示詞模板：{c_id}"}
        updates["cleanup"] = {"prompt_template": c_id}

    if summary_template:
        try:
            s_id = _sanitize_template_id(summary_template)
        except ValueError as ve:
            return {"status": "error", "message": str(ve)}
        if not resolve_prompt_file(s_id, base_dir=base_dir).is_file():
            return {"status": "error", "message": f"找不到總結提示詞模板：{s_id}"}
        updates["summarize"] = {"template": s_id}

    if updates:
        update_pipeline_config(updates)

    payload = get_prompt_templates_payload()
    if preset_label:
        payload["message"] = f"已套用場景預設：{preset_label}"
    else:
        payload["message"] = "已更新 AI 整理 / 總結提示詞模板設定"
    return payload


def save_prompt_template_action(
    template_id: str,
    stage: str,
    name: str,
    category: str,
    description: str,
    content: str,
    set_active: bool = True,
) -> Dict[str, Any]:
    """Save custom or overridden prompt template and optionally set it active."""
    from lecture_pipeline.prompt_catalog import save_prompt_template

    base_dir = CONFIG_FILE_PATH.resolve().parent
    try:
        res = save_prompt_template(
            template_id=template_id,
            stage=stage,
            name=name,
            category=category,
            description=description,
            content=content,
            base_dir=base_dir,
        )
    except ValueError as ve:
        return {"status": "error", "message": str(ve)}

    if res.get("status") != "ok":
        return res

    if set_active:
        saved_id = res["template_id"]
        if res["stage"] == "cleanup":
            update_pipeline_config({"cleanup": {"prompt_template": saved_id}})
        else:
            update_pipeline_config({"summarize": {"template": saved_id}})

    payload = get_prompt_templates_payload()
    payload["saved_id"] = res["template_id"]
    payload["message"] = res["message"]
    return payload


def reset_prompt_template_action(template_id: str) -> Dict[str, Any]:
    """Reset a modified built-in prompt template back to factory default."""
    from lecture_pipeline.prompt_catalog import reset_prompt_template

    base_dir = CONFIG_FILE_PATH.resolve().parent
    try:
        res = reset_prompt_template(template_id=template_id, base_dir=base_dir)
    except ValueError as ve:
        return {"status": "error", "message": str(ve)}

    if res.get("status") != "ok":
        return res

    payload = get_prompt_templates_payload()
    payload["message"] = res["message"]
    return payload


def delete_prompt_template_action(template_id: str) -> Dict[str, Any]:
    """Delete a user-created custom prompt template, resetting active selection if needed."""
    from lecture_pipeline.prompt_catalog import _sanitize_template_id, delete_custom_prompt_template

    base_dir = CONFIG_FILE_PATH.resolve().parent
    try:
        clean_id = _sanitize_template_id(template_id)
        res = delete_custom_prompt_template(clean_id, base_dir=base_dir)
    except ValueError as ve:
        return {"status": "error", "message": str(ve)}

    if res.get("status") != "ok":
        return res

    cfg = get_current_config()
    fallback_updates: Dict[str, Any] = {}
    if Path(str(cfg.get("cleanup", {}).get("prompt_template") or "")).name == clean_id:
        fallback_updates["cleanup"] = {"prompt_template": "videolingo_punct.md"}
    if Path(str(cfg.get("summarize", {}).get("template") or "")).name == clean_id:
        fallback_updates["summarize"] = {"template": "fabric_summarize_lecture.md"}
    if fallback_updates:
        update_pipeline_config(fallback_updates)

    payload = get_prompt_templates_payload()
    payload["message"] = res["message"]
    return payload
