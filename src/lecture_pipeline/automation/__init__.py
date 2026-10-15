"""Automation package containing watcher and quota management."""

from lecture_pipeline.automation.quota import DeferredTask, QuotaManager
from lecture_pipeline.automation.watcher import (
    is_file_stable,
    run_pipeline_for_group,
    scan_and_group_inbox,
    watch_inbox,
)

__all__ = [
    "DeferredTask",
    "QuotaManager",
    "is_file_stable",
    "run_pipeline_for_group",
    "scan_and_group_inbox",
    "watch_inbox",
]
