"""Filename parser and lecture routing module.

Handles:
1. Standard pattern: CourseName_YYYYMMDD[_HHMM]
2. University course pattern: Course Name  02, 通識 音樂 04 (2), Discrete Mathematics 13
3. SONY ICD-PX470 native recorder pattern: YYMMDD_HHMM
4. Embedded date / Modification time fallback
Routing audio to work/<Course>/<Date>[_L<LectureNum>]/ directories.
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
    lecture_num: Optional[str] = None
    part: Optional[str] = None
    warning: Optional[str] = None

    @property
    def display_tag(self) -> str:
        """Formatted tag for logging and display."""
        parts = [self.course]
        date_part = self.date
        if self.time_str:
            date_part += f"_{self.time_str}"
        if self.lecture_num:
            date_part += f" (第 {self.lecture_num} 講"
            if self.part:
                date_part += f" Part {self.part}"
            date_part += ")"
        parts.append(date_part)
        return f"[{' | '.join(parts)}]"


# Standard pattern: CourseName_YYYYMMDD or CourseName_YYYYMMDD_HHMM
STANDARD_PATTERN = re.compile(
    r"^(?P<course>[A-Za-z0-9\-]+)_(?P<date>\d{8})(?:_(?P<time>\d{4,6}))?$",
    re.IGNORECASE,
)

# University course pattern: Course Name  02, 通識 音樂 04 (2), Discrete Mathematics 13
# Allows multi-word English or Chinese course names, optional multiple spaces, lecture index, and optional (part)
COURSE_LECTURE_PATTERN = re.compile(
    r"^(?P<course>.+?)\s+(?P<lec_num>\d+)(?:\s*\((?P<part>\d+)\))?$",
    re.UNICODE,
)

# SONY ICD-PX470 native pattern: YYMMDD_HHMM (e.g., 261015_0930)
SONY_RECORDER_PATTERN = re.compile(
    r"^(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})_(?P<time>\d{4})$"
)

# Fallback date detector: any 8-digit date YYYYMMDD in the filename
DATE_DETECTOR_PATTERN = re.compile(r"(?P<date>20\d{6})")


def get_file_mtime_date(path: Path) -> tuple[str, str]:
    """Retrieve date (YYYYMMDD) and time (HHMM) from file modification timestamp."""
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return mtime.strftime("%Y%m%d"), mtime.strftime("%H%M")
    except Exception:
        now = datetime.now()
        return now.strftime("%Y%m%d"), now.strftime("%H%M")


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
    raw_stem = path.stem.strip()
    base_work = Path(base_work_dir)

    # 1. Try standard pattern: CourseName_YYYYMMDD[_HHMM]
    match = STANDARD_PATTERN.match(raw_stem)
    if match:
        course = match.group("course").strip()
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

    # 2. Try university course + lecture number pattern: Course Name 02, 通識 音樂 04 (2)
    match_course = COURSE_LECTURE_PATTERN.match(raw_stem)
    if match_course:
        raw_course = match_course.group("course").strip()
        # Collapse multiple internal spaces
        course = re.sub(r"\s+", " ", raw_course)
        lec_num = match_course.group("lec_num")
        part = match_course.group("part")

        # Try to find embedded date, or fallback to file modification timestamp
        match_date = DATE_DETECTOR_PATTERN.search(raw_stem)
        if match_date:
            date_str = match_date.group("date")
            time_str = None
        else:
            date_str, time_str = get_file_mtime_date(path)

        dir_name = f"{date_str}_L{lec_num}"
        work_dir = base_work / course / dir_name

        return ParsedLectureInfo(
            course=course,
            date=date_str,
            time_str=time_str,
            lecture_num=lec_num,
            part=part,
            is_standard=True,
            original_filename=path.name,
            source_path=path.resolve(),
            work_dir=work_dir.resolve(),
            warning=None,
        )

    # 3. Try SONY recorder native pattern (e.g. 261015_0930 -> 20261015)
    match_sony = SONY_RECORDER_PATTERN.match(raw_stem)
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

    # 4. Fallback: check if an 8-digit date is embedded anywhere in the filename
    match_date = DATE_DETECTOR_PATTERN.search(raw_stem)
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

    # 5. Ultimate Fallback: use file modification time
    date_str, time_str = get_file_mtime_date(path)
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
