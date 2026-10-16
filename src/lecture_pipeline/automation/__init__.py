"""Automation package containing watcher, tray, autostart, notifications, and quota management."""

from lecture_pipeline.automation.autostart import (
    disable_autostart,
    enable_autostart,
    get_autostart_status,
)
from lecture_pipeline.automation.notifier import (
    notify_corrupted_file,
    notify_offline_pending,
    notify_processing_started,
    notify_upload_success,
    send_notification,
)
from lecture_pipeline.automation.offline_queue import (
    OfflineUploadQueue,
    is_internet_connected,
)
from lecture_pipeline.automation.pruner import (
    compress_audio_to_opus,
    get_record_archive_base_dir,
    prune_and_archive,
)
from lecture_pipeline.automation.quota import DeferredTask, QuotaManager
from lecture_pipeline.automation.tray import WatcherTray, create_status_icon
from lecture_pipeline.automation.watcher import (
    DeadLetterQuarantine,
    is_file_stable,
    run_pipeline_for_group,
    run_pipeline_with_timeout,
    scan_and_group_inbox,
    watch_inbox,
)

__all__ = [
    "DeadLetterQuarantine",
    "DeferredTask",
    "OfflineUploadQueue",
    "QuotaManager",
    "WatcherTray",
    "compress_audio_to_opus",
    "create_status_icon",
    "disable_autostart",
    "enable_autostart",
    "get_autostart_status",
    "get_record_archive_base_dir",
    "is_file_stable",
    "is_internet_connected",
    "notify_corrupted_file",
    "notify_offline_pending",
    "notify_processing_started",
    "notify_upload_success",
    "prune_and_archive",
    "run_pipeline_for_group",
    "run_pipeline_with_timeout",
    "scan_and_group_inbox",
    "send_notification",
    "watch_inbox",
]
