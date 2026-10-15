"""Filename parser and lecture routing module.

Handles standard, recorder-native, and irregular filenames,
routing audio to work/<Course>/<Date>/ directories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ParsedLectureInfo:
    """Information parsed from an incoming lecture audio filename."""

    course: str
    date: str  # YYYYMMDD
    time_str: Optional[str]  # HHMM or None
    is_standard: bool
    original_filename: str
    source_path: Path
    work_dir: Path
    warning: Optional[str] = None

    @property
    def display_tag(self) -> str:
        """Formatted tag for logging and display."""
        if self.time_str:
            return f"[{self.course} | {self.date}_{self.time_str}]"
        return f"[{self.course} | {self.date}]"


# Standard pattern: CourseName_YYYYMMDD or CourseName_YYYYMMDD_HHMM
# CourseName: alphanumeric and hyphens
STANDARD_PATTERN = re.compile(
    r"^(?P<course>[A-Za-z0-9\-]+)_(?P<date>\d{8})(?:_(?P<time>\d{4,6}))?$",
    re.IGNORECASE,
)

# SONY ICD-PX470 native pattern: YYMMDD_HHMM (e.g., 261015_0930)
SONY_RECORDER_PATTERN = re.compile(
    r"^(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})_(?P<time>\d{4})$"
)

# Fallback date detector: any 8-digit date YYYYMMDD in the filename
DATE_DETECTOR_PATTERN = re.compile(r"(?P<date>20\d{6})")


def parse_filename(
    filepath: Path | str,
    base_work_dir: Path | str = "work",
) -> ParsedLectureInfo:
    """Parse audio filename and resolve target course and work directory.

    Args:
        filepath: Path to audio file.
        base_work_dir: Base directory for active workspaces (default: 'work').

    Returns:
        ParsedLectureInfo with course, date, work_dir, and status flags.
    """
    path = Path(filepath)
    stem = path.stem
    base_work = Path(base_work_dir)

    # 1. Try standard pattern
    match = STANDARD_PATTERN.match(stem)
    if match:
        course = match.group("course")
        date_str = match.group("date")
        time_str = match.group("time")
        work_dir = base_work / course / date_str
        return ParsedLectureInfo(
            course=course,
            date=date_str,
            time_str=time_str,
            is_standard=True,
            original_filename=path.name,
            source_path=path.resolve(),
            work_dir=work_dir.resolve(),
            warning=None,
        )

    # 2. Try SONY recorder native pattern (e.g. 261015_0930 -> 20261015)
    match_sony = SONY_RECORDER_PATTERN.match(stem)
    if match_sony:
        yy = match_sony.group("yy")
        mm = match_sony.group("mm")
        dd = match_sony.group("dd")
        date_str = f"20{yy}{mm}{dd}"
        time_str = match_sony.group("time")
        course = "Unsorted"
        work_dir = base_work / course / date_str
        warning = (
            f"檔案 '{path.name}' 符合錄音筆原生命名格式 (YYMMDD_HHMM)。"
            f"已自動識別日期為 {date_str}，課程暫歸類為 '{course}'。"
        )
        return ParsedLectureInfo(
            course=course,
            date=date_str,
            time_str=time_str,
            is_standard=False,
            original_filename=path.name,
            source_path=path.resolve(),
            work_dir=work_dir.resolve(),
            warning=warning,
        )

    # 3. Fallback: check if an 8-digit date is embedded anywhere in the filename
    match_date = DATE_DETECTOR_PATTERN.search(stem)
    if match_date:
        date_str = match_date.group("date")
        course = "Unsorted"
        work_dir = base_work / course / date_str
        warning = (
            f"檔案 '{path.name}' 非標準命名，已從檔名提取日期 {date_str}，"
            f"課程暫歸類為 '{course}'。"
        )
        return ParsedLectureInfo(
            course=course,
            date=date_str,
            time_str=None,
            is_standard=False,
            original_filename=path.name,
            source_path=path.resolve(),
            work_dir=work_dir.resolve(),
            warning=warning,
        )

    # 4. Ultimate Fallback: use file modification time
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        date_str = mtime.strftime("%Y%m%d")
        time_str = mtime.strftime("%H%M")
    except Exception:
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%H%M")

    course = "Unsorted"
    work_dir = base_work / course / date_str
    warning = (
        f"檔案 '{path.name}' 無法辨識課程與日期，已依檔案修改時間設定為 "
        f"{date_str}，課程暫歸類為 '{course}'。"
    )

    return ParsedLectureInfo(
        course=course,
        date=date_str,
        time_str=time_str,
        is_standard=False,
        original_filename=path.name,
        source_path=path.resolve(),
        work_dir=work_dir.resolve(),
        warning=warning,
    )
