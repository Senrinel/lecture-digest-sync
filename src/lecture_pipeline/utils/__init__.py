"""Utilities package for lecture pipeline."""

from lecture_pipeline.utils.bootstrap import (
    check_ffmpeg_ready,
    check_local_stt_ready,
    get_app_base_dir,
    get_os_summary,
    install_ffmpeg_locally,
    is_valid_api_key_value,
    is_webview2_available,
    repair_and_init_environment,
    save_gemini_api_key,
    save_google_credentials_json,
)
from lecture_pipeline.utils.process import (
    NO_WINDOW_FLAGS,
    resolve_binary,
    silent_popen,
    silent_run,
)

__all__ = [
    "NO_WINDOW_FLAGS",
    "check_ffmpeg_ready",
    "check_local_stt_ready",
    "get_app_base_dir",
    "get_os_summary",
    "install_ffmpeg_locally",
    "is_valid_api_key_value",
    "is_webview2_available",
    "repair_and_init_environment",
    "resolve_binary",
    "save_gemini_api_key",
    "save_google_credentials_json",
    "silent_popen",
    "silent_run",
]
