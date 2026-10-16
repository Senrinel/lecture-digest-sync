"""Windows desktop notifications module for lecture pipeline events."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lecture_pipeline.notifier")

# Find default icon if present
DEFAULT_ICON_PATH = Path("icon.png").resolve()


def send_notification(
    title: str,
    message: str,
    icon_path: Optional[Path | str] = None,
    sync: bool = False,
) -> None:
    """Send a native Windows 10/11 desktop toast notification.

    Args:
        title: Notification header text.
        message: Notification body text.
        icon_path: Optional path to icon image.
        sync: If True, wait for notification; if False (default), run in daemon thread.
    """
    def _do_send():
        try:
            # Check for win11toast
            import win11toast

            actual_icon = None
            if icon_path and Path(icon_path).is_file():
                actual_icon = str(Path(icon_path).resolve())
            elif DEFAULT_ICON_PATH.is_file():
                actual_icon = str(DEFAULT_ICON_PATH)

            kwargs = {
                "app_id": "ASR 課堂自動化系統",
            }
            if actual_icon:
                kwargs["icon"] = actual_icon

            win11toast.toast(title, message, **kwargs)
        except Exception as e:
            # Fallback to logger / PowerShell
            logger.debug(f"Failed to send toast via win11toast ({e}), falling back to log.")
            try:
                # In headless/test or fallback, log the event
                print(f"[桌面通知] {title}: {message}")
            except Exception:
                pass

    if sync:
        _do_send()
    else:
        thread = threading.Thread(target=_do_send, daemon=True, name="ToastNotifier")
        thread.start()


def notify_processing_started(filename: str, sync: bool = False) -> None:
    """Notify user that audio processing has started.
    
    User specification: 「"{檔名}" 已開始解析」
    """
    send_notification(
        title="課堂錄音處理",
        message=f'"{filename}" 已開始解析',
        sync=sync,
    )


def notify_upload_success(filename: str, doc_url: Optional[str] = None, sync: bool = False) -> None:
    """Notify user that transcript was successfully uploaded to Google Docs.
    
    User specification: 「"{檔名}" 上傳成功」
    """
    send_notification(
        title="Google 文件同步",
        message=f'"{filename}" 上傳成功',
        sync=sync,
    )


def notify_offline_pending(filename: str, sync: bool = False) -> None:
    """Notify user that audio processing completed offline, queued for upload.
    
    User specification: 「"{檔名}" 處理完成，等候上傳」
    """
    send_notification(
        title="離線排隊中",
        message=f'"{filename}" 處理完成，等候上傳',
        sync=sync,
    )


def notify_corrupted_file(filename: str, sync: bool = False) -> None:
    """Notify user that file failed repeatedly and is isolated as corrupted.
    
    User specification: 「"{檔名}" 損壞，放棄處理」
    """
    send_notification(
        title="壞檔隔離警報",
        message=f'"{filename}" 損壞，放棄處理',
        sync=sync,
    )
