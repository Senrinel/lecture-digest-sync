"""Environment bootstrap, diagnostic checks, credential setup, and self-repair utilities.

Ensures a clean standalone machine without prior Python/FFmpeg/.env/credentials can:
1. Validate and self-repair config.yaml, working directories, and corrupted JSON states.
2. Detect or auto-install portable FFmpeg/FFprobe binaries into app `bin/`.
3. Detect WebView2 Runtime availability and gracefully downgrade to browser mode when absent.
4. Provide guided verification & storage for Gemini API Key and Google OAuth credentials.json.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
import yaml

from lecture_pipeline.utils.process import resolve_binary, silent_run

FFMPEG_PORTABLE_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

_PLACEHOLDER_KEY_MARKERS = (
    "請在此填入",
    "your_api_key",
    "your_actual",
    "gemini_api_key_here",
    "placeholder",
    "<your",
    "replace_with",
)


def get_app_base_dir() -> Path:
    """Return application root directory (executable folder when frozen, repo root otherwise)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def is_valid_api_key_value(val: Optional[str]) -> bool:
    """Check whether a Gemini API key is non-empty and not an example placeholder."""
    if not val:
        return False
    cleaned = val.strip().strip('"').strip("'")
    if len(cleaned) < 10:
        return False
    lower = cleaned.lower()
    return not any(marker in lower for marker in _PLACEHOLDER_KEY_MARKERS)


def is_webview2_available() -> bool:
    """Check whether Microsoft Edge WebView2 Runtime is installed on Windows."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        candidates = [
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}"),
        ]
        for hive, subkey in candidates:
            try:
                with winreg.OpenKey(hive, subkey) as k:
                    pv, _ = winreg.QueryValueEx(k, "pv")
                    if pv and str(pv).strip() != "0.0.0.0":
                        return True
            except OSError:
                continue
    except Exception:
        pass
    return False


def check_ffmpeg_ready() -> Tuple[bool, str, str]:
    """Verify ffmpeg and ffprobe executables are resolvable and runnable.

    Returns:
        (ok, label, resolved_path)
    """
    ffmpeg_bin = resolve_binary("ffmpeg")
    ffprobe_bin = resolve_binary("ffprobe")
    try:
        res = silent_run([ffmpeg_bin, "-version"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and "ffmpeg version" in res.stdout.lower():
            # Also confirm ffprobe resolves cleanly
            probe_ok = False
            try:
                p_res = silent_run([ffprobe_bin, "-version"], capture_output=True, text=True, timeout=5)
                probe_ok = p_res.returncode == 0
            except Exception:
                probe_ok = False

            if probe_ok:
                return True, f"FFmpeg 已就緒 ({Path(ffmpeg_bin).parent})", ffmpeg_bin
            return True, f"FFmpeg 已就緒 (缺少 ffprobe: {ffmpeg_bin})", ffmpeg_bin
    except Exception:
        pass

    return False, "缺少 FFmpeg / FFprobe（無法處理音訊）", ""


def install_ffmpeg_locally(
    progress_cb: Optional[Callable[[str, int], None]] = None,
    target_bin_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Download and extract portable ffmpeg.exe & ffprobe.exe into app bin/ without admin rights."""
    bin_dir = Path(target_bin_dir).resolve() if target_bin_dir else (get_app_base_dir() / "bin")
    bin_dir.mkdir(parents=True, exist_ok=True)

    if progress_cb:
        progress_cb("正在連線下載官方 FFmpeg 套件...", 10)

    tmp_dir = Path(tempfile.mkdtemp(prefix="lp_ffmpeg_dl_"))
    zip_path = tmp_dir / "ffmpeg-essentials.zip"

    try:
        req = urllib.request.Request(
            FFMPEG_PORTABLE_URL,
            headers={"User-Agent": "LecturePipeline-Bootstrap/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(zip_path, "wb") as out_f:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total > 0:
                        pct = min(75, 10 + int(downloaded * 65 / total))
                        progress_cb(
                            f"下載中 ({downloaded / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB)...",
                            pct,
                        )

        if progress_cb:
            progress_cb("正在解壓縮 ffmpeg.exe 與 ffprobe.exe...", 80)

        extracted = []
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                base_name = Path(member.filename).name.lower()
                if base_name in ("ffmpeg.exe", "ffprobe.exe") and not member.is_dir():
                    dest = bin_dir / base_name
                    with zf.open(member) as src_s, open(dest, "wb") as dst_f:
                        shutil.copyfileobj(src_s, dst_f)
                    extracted.append(base_name)

        if "ffmpeg.exe" not in extracted:
            return False, "下載壓縮檔中找不到 ffmpeg.exe，請改由手動下載安裝。"

        if progress_cb:
            progress_cb("正在驗證 FFmpeg 執行相容性...", 95)

        ok, msg, _ = check_ffmpeg_ready()
        if ok:
            return True, f"FFmpeg 安裝成功：{bin_dir}"
        return False, "已解壓縮執行檔但無法啟動，請檢查防毒軟體是否封鎖。"
    except Exception as e:
        return False, f"自動下載安裝 FFmpeg 失敗 ({e})，請確認網路連線或手動安裝。"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def check_local_stt_ready() -> Tuple[bool, str]:
    """Check whether isolated local faster-whisper backend environment is installed."""
    base = get_app_base_dir()
    if sys.platform == "win32":
        py_exe = base / "backends" / "fasterwhisper" / ".venv" / "Scripts" / "python.exe"
    else:
        py_exe = base / "backends" / "fasterwhisper" / ".venv" / "bin" / "python"

    if py_exe.is_file():
        return True, "CTranslate2 本機 GPU 後端已就緒"
    return False, "未安裝本機 Whisper 環境（預設使用雲端 C1 通道，運作不受影響）"


def is_foreign_user_path(raw_path: str) -> bool:
    """Detect mojibake or paths pointing to a non-existent foreign Windows user profile."""
    if not raw_path or not isinstance(raw_path, str):
        return False
    if any(bad in raw_path for bad in ("\ufffd", "Ұ", "?")):
        return True

    m = re.match(r"^[A-Za-z]:[\\/]+Users[\\/]+([^\\/]+)", raw_path.strip(), flags=re.IGNORECASE)
    if m:
        profile_name = m.group(1)
        cur_home = Path.home()
        if profile_name.lower() != cur_home.name.lower():
            candidate_user_dir = Path(raw_path[: m.end()])
            if not candidate_user_dir.exists():
                return True
    return False


def get_default_config_dict() -> Dict[str, Any]:
    """Return a clean, user-agnostic default configuration dictionary."""
    docs_dir = Path.home() / "Documents" / "課堂錄音"
    return {
        "paths": {
            "inbox_dir": str(docs_dir / "收件匣"),
            "work_dir": "work",
            "archive_dir": "archive",
            "record_archive_dir": str(docs_dir / "錄音封存"),
            "logs_dir": "logs",
            "benchmark_dir": "benchmark",
            "prompts_dir": "prompts",
            "courses_dir": str(docs_dir / "科目詞庫"),
            "exports_dir": str(docs_dir / "筆記匯出"),
        },
        "preprocessing": {
            "hp": "100Hz",
            "loud": "-19LUFS",
            "dyn": "off",
            "nr": "off",
            "dfn": "off",
            "se": "off",
            "wpe": "off",
            "vadtrim": "off",
        },
        "segmentation": {
            "target_chunk_sec": 600,
            "search_window_sec": 30,
            "silence_thresh_db": -40,
            "min_silence_sec": 0.5,
            "overlap_sec": 0,
        },
        "stt": {
            "default_channel": "C1",
            "fallback_local_channel": "C7",
            "prompt_style": "verbatim",
            "temperature": 0.0,
            "opencc_config": "s2twp",
        },
        "cleanup": {
            "mode": "text",
            "model": "gemini-3.5-flash-lite",
            "prompt_template": "videolingo_punct.md",
            "chunk_chars": 1500,
            "overlap_chars": 300,
            "length_ratio_min": 0.8,
            "length_ratio_max": 1.2,
        },
        "summarize": {
            "model": "gemini-3.5-flash-lite",
            "template": "fabric_summarize_lecture.md",
            "thinking_budget": 1024,
        },
        "export": {
            "export_dir": str(docs_dir / "筆記匯出"),
            "auto_open_notebooklm": False,
            "notebooklm_url": "https://notebooklm.google.com/",
        },
        "quota_policy": {
            "on_quota_exhausted": "local_then_defer",
            "daily_reset_hour_utc": 8,
        },
        "timetable": {
            "enabled": False,
            "Monday": None,
            "Tuesday": None,
            "Wednesday": None,
            "Thursday": None,
            "Friday": None,
            "Saturday": None,
            "Sunday": None,
        },
        "gui": {
            "mode": "window" if is_webview2_available() else "web",
            "host": "127.0.0.1",
            "port": 8000,
            "auto_open_browser": True,
            "window_width": 1280,
            "window_height": 800,
        },
    }


def _repair_corrupt_json_file(file_path: Path, default_value: Any = None) -> Optional[str]:
    """Back up and reset/remove a corrupted JSON file so startup or reinstall recovers cleanly."""
    if not file_path.is_file():
        return None
    try:
        content = file_path.read_text(encoding="utf-8-sig").strip()
        if not content:
            raise ValueError("empty json")
        json.loads(content)
        return None
    except Exception:
        bak = file_path.with_name(file_path.name + ".corrupt.bak")
        try:
            shutil.copy2(file_path, bak)
        except Exception:
            pass
        if default_value is not None:
            file_path.write_text(json.dumps(default_value, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            try:
                file_path.unlink()
            except Exception:
                pass
        return f"已自動修復損壞狀態檔: {file_path.name}"


def repair_and_init_environment(
    inbox_dir: Optional[str] = None,
    exports_dir: Optional[str] = None,
    courses_dir: Optional[str] = None,
    record_archive_dir: Optional[str] = None,
    config_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Validate, initialize, and self-repair config.yaml, user folders, and corrupted state files."""
    base = get_app_base_dir()
    cfg_file = Path(config_path).resolve() if config_path else (base / "config.yaml")
    defaults = get_default_config_dict()
    repairs: list[str] = []

    # 1. Load or repair config.yaml
    cfg: Dict[str, Any] = {}
    if cfg_file.is_file():
        try:
            loaded = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg = loaded
            else:
                raise ValueError("config root is not mapping")
        except Exception:
            bak = cfg_file.with_name("config.yaml.corrupt.bak")
            try:
                shutil.copy2(cfg_file, bak)
            except Exception:
                pass
            cfg = {}
            repairs.append("已備份並重建損壞的 config.yaml")

    # Merge missing top-level defaults without clobbering existing valid user preferences
    for section, default_sec in defaults.items():
        if section not in cfg or not isinstance(cfg.get(section), dict):
            cfg[section] = dict(default_sec) if isinstance(default_sec, dict) else default_sec
        elif isinstance(default_sec, dict):
            for k, v in default_sec.items():
                if k not in cfg[section]:
                    cfg[section][k] = v

    # 2. Initialize / sanitize user directories
    docs_dir = Path.home() / "Documents" / "課堂錄音"
    default_user_paths = {
        "inbox_dir": str(docs_dir / "收件匣"),
        "exports_dir": str(docs_dir / "筆記匯出"),
        "courses_dir": str(docs_dir / "科目詞庫"),
        "record_archive_dir": str(docs_dir / "錄音封存"),
    }
    explicit_map = {
        "inbox_dir": inbox_dir,
        "exports_dir": exports_dir,
        "courses_dir": courses_dir,
        "record_archive_dir": record_archive_dir,
    }

    paths_sec = cfg["paths"]
    for key, default_val in default_user_paths.items():
        explicit_val = explicit_map.get(key)
        if explicit_val:
            chosen = default_val if is_foreign_user_path(explicit_val) else explicit_val
            paths_sec[key] = chosen
        else:
            cur_val = str(paths_sec.get(key, "") or "")
            # Upgrade generic template defaults or broken foreign user paths to current user Documents
            is_generic_template = cur_val in ("", "inbox", "exports", "courses", "record_archive")
            if is_generic_template or is_foreign_user_path(cur_val):
                paths_sec[key] = default_val
                if not is_generic_template:
                    repairs.append(f"已修正無效路徑 {key} -> {default_val}")

    # Keep export.export_dir synchronized when generic or foreign
    cur_exp_dir = str(cfg["export"].get("export_dir", "") or "")
    if exports_dir or cur_exp_dir in ("", "exports") or is_foreign_user_path(cur_exp_dir):
        cfg["export"]["export_dir"] = paths_sec["exports_dir"]

    # Ensure required directories exist & verify write access
    for dir_key in ("inbox_dir", "exports_dir", "courses_dir", "record_archive_dir"):
        p = Path(paths_sec[dir_key])
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            fallback = Path(default_user_paths[dir_key])
            fallback.mkdir(parents=True, exist_ok=True)
            paths_sec[dir_key] = str(fallback)
            if dir_key == "exports_dir":
                cfg["export"]["export_dir"] = str(fallback)
            repairs.append(f"目錄無寫入權限，已改用 {fallback}")

    # 3. Downgrade window mode gracefully if WebView2 Runtime is missing
    if cfg.get("gui", {}).get("mode") in ("window", "both") and not is_webview2_available():
        cfg["gui"]["mode"] = "web"
        repairs.append("系統未安裝 WebView2 Runtime，已自動切換為瀏覽器模式")

    # 4. Save repaired config.yaml
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # 5. Repair any corrupted JSON runtime state files
    for msg in [
        _repair_corrupt_json_file(base / "pending_uploads" / "queue.json", []),
        _repair_corrupt_json_file(base / "quota_lock.json", None),
        _repair_corrupt_json_file(base / "token.json", None),
        _repair_corrupt_json_file(Path(paths_sec["inbox_dir"]) / ".retry_stats.json", {}),
    ]:
        if msg:
            repairs.append(msg)

    # Clear stale processing lock files if left over after a crash
    for stale_lock in base.glob("*.lock"):
        try:
            stale_lock.unlink()
        except Exception:
            pass

    return {
        "status": "ok",
        "config_file": str(cfg_file),
        "paths": {
            "inbox_dir": paths_sec["inbox_dir"],
            "exports_dir": paths_sec["exports_dir"],
            "courses_dir": paths_sec["courses_dir"],
            "record_archive_dir": paths_sec["record_archive_dir"],
        },
        "repairs": repairs,
    }


def save_gemini_api_key(
    api_key: str,
    verify: bool = True,
    env_path: Optional[Path | str] = None,
) -> Tuple[bool, str]:
    """Validate and persist GEMINI_API_KEY into .env and live process environment."""
    cleaned = (api_key or "").strip().strip('"').strip("'")
    if not is_valid_api_key_value(cleaned):
        return False, "API 金鑰格式無效或為範例文字，請貼入真實的 Gemini API 金鑰。"

    if verify:
        try:
            from google import genai

            client = genai.Client(api_key=cleaned)
            list(client.models.list(config={"page_size": 1}))
        except Exception as e:
            return False, f"API 金鑰驗證連線失敗：{e}"

    target_env = Path(env_path).resolve() if env_path else (get_app_base_dir() / ".env")
    lines = []
    found = False
    if target_env.is_file():
        for raw_line in target_env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if raw_line.strip().startswith("GEMINI_API_KEY="):
                lines.append(f'GEMINI_API_KEY="{cleaned}"')
                found = True
            else:
                lines.append(raw_line)
    if not found:
        lines.append(f'GEMINI_API_KEY="{cleaned}"')

    target_env.parent.mkdir(parents=True, exist_ok=True)
    target_env.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.environ["GEMINI_API_KEY"] = cleaned
    return True, "Gemini API 金鑰已驗證並成功儲存。"


def save_google_credentials_json(
    raw_content: bytes | str,
    creds_path: Optional[Path | str] = None,
) -> Tuple[bool, str]:
    """Validate and save Google Cloud OAuth client credentials.json."""
    try:
        text = raw_content.decode("utf-8-sig") if isinstance(raw_content, (bytes, bytearray)) else str(raw_content)
        data = json.loads(text)
    except Exception as e:
        return False, f"檔案並非有效的 JSON 格式：{e}"

    client_cfg = data.get("installed") or data.get("web")
    if not isinstance(client_cfg, dict) or not client_cfg.get("client_id") or not client_cfg.get("client_secret"):
        return False, "這不是有效的 Google OAuth 用戶端憑證 (需包含 installed/web client_id 與 client_secret)。"

    target_creds = Path(creds_path).resolve() if creds_path else (get_app_base_dir() / "credentials.json")
    target_creds.parent.mkdir(parents=True, exist_ok=True)
    target_creds.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, "Google OAuth 憑證 (credentials.json) 已成功匯入，請點擊「授權 Google 帳號」完成登入。"


def get_os_summary() -> str:
    """Return real OS and Python runtime version string."""
    try:
        sys_name = platform.system()
        rel = platform.release()
        if sys_name == "Windows" and sys.getwindowsversion().build >= 22000:
            rel = "11"
        py_ver = ".".join(platform.python_version_tuple()[:2])
        return f"{sys_name} {rel} / Python {py_ver} (UTF-8)"
    except Exception:
        return f"{sys.platform} / Python {sys.version.split()[0]}"
