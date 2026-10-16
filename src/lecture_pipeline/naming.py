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
# Allows Unicode/Chinese course names, alphanumeric, hyphens, spaces: e.g. 演算法_20261015, DataMining_20261015
STANDARD_PATTERN = re.compile(
    r"^(?P<course>[^_]+?)_(?P<date>\d{8})(?:_(?P<time>\d{4,6}))?$",
    re.UNICODE,
)

# University course pattern: Course Name  02, 通識 音樂 04 (2), Discrete Mathematics 13, Engineering Mathematics 12 C
# Allows multi-word English or Chinese course names, optional multiple spaces, lecture index, optional section letter, and optional (part)
COURSE_LECTURE_PATTERN = re.compile(
    r"^(?P<course>.+?)\s+(?P<lec_num>\d+)(?:\s+(?P<section>[A-Za-z]))?(?:\s*\((?P<part>\d+)\))?$",
    re.UNICODE,
)

# SONY ICD-PX470 native pattern: YYMMDD_HHMM (e.g., 261015_0930)
SONY_RECORDER_PATTERN = re.compile(
    r"^(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})_(?P<time>\d{4})$"
)

# Fallback date detector: any 8-digit date YYYYMMDD in the filename
DATE_DETECTOR_PATTERN = re.compile(r"(?P<date>20\d{6})")

# Generic non-descriptive audio recording names that should route to 'Unsorted'
GENERIC_AUDIO_NAMES = {
    "voice_memo",
    "voice-memo",
    "voicememo",
    "recording",
    "record",
    "audio",
    "memo",
    "untitled",
    "track",
    "sound",
}


def get_file_mtime_date(path: Path) -> tuple[str, str]:
    """Retrieve date (YYYYMMDD) and time (HHMM) from file modification timestamp."""
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return mtime.strftime("%Y%m%d"), mtime.strftime("%H%M")
    except Exception:
        now = datetime.now()
        return now.strftime("%Y%m%d"), now.strftime("%H%M")


def match_course_from_timetable(
    date_str: str,
    time_str: Optional[str],
    config_path: Path | str = "config.yaml",
) -> Optional[str]:
    """Match course name from timetable based on recording date and time."""
    if not date_str or not time_str or len(time_str) < 4:
        return None

    try:
        cfg = Path(config_path)
        if not cfg.is_file():
            return None

        import yaml
        with open(cfg, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        timetable = data.get("timetable", {})
        if not timetable or not timetable.get("enabled", True):
            return None

        dt = datetime.strptime(date_str, "%Y%m%d")
        weekday = dt.strftime("%A")

        slots = timetable.get(weekday, [])
        if not slots or not isinstance(slots, list):
            return None

        rec_hour = int(time_str[:2])
        rec_min = int(time_str[2:4])
        rec_mins = rec_hour * 60 + rec_min

        for slot in slots:
            slot_time = slot.get("time", "")
            target_course = slot.get("course")
            if not slot_time or not target_course or "-" not in slot_time:
                continue

            start_s, end_s = slot_time.split("-", 1)
            sh, sm = map(int, start_s.strip().split(":"))
            eh, em = map(int, end_s.strip().split(":"))
            start_mins = sh * 60 + sm
            end_mins = eh * 60 + em

            # Allow 30 mins early or during class
            if (start_mins - 30) <= rec_mins <= end_mins:
                return str(target_course).strip()

    except Exception:
        pass
    return None


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
    # Strip any internal duplicate audio extension (e.g. "Engineering Mathematics 12.mp3 C" -> "Engineering Mathematics 12 C")
    cleaned_stem = re.sub(r"\.(mp3|wav|m4a|aac|flac|ogg)\b", "", raw_stem, flags=re.IGNORECASE).strip()
    # Strip leading dots, underscores, or spaces (e.g. ".畢業流程說明" -> "畢業流程說明")
    cleaned_stem = cleaned_stem.lstrip("._ ").strip()
    base_work = Path(base_work_dir)

    # 1. Try standard pattern: CourseName_YYYYMMDD[_HHMM]
    match = STANDARD_PATTERN.match(cleaned_stem)
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
    match_course = COURSE_LECTURE_PATTERN.match(cleaned_stem)
    if match_course:
        raw_course = match_course.group("course").strip()
        course = re.sub(r"\s+", " ", raw_course)
        lec_num = match_course.group("lec_num")
        section = match_course.group("section")
        part = match_course.group("part")
        lec_num_full = f"{lec_num}{section.upper()}" if section else lec_num

        match_date = DATE_DETECTOR_PATTERN.search(cleaned_stem)
        if match_date:
            date_str = match_date.group("date")
            time_str = None
        else:
            date_str, time_str = get_file_mtime_date(path)

        dir_name = f"{date_str}_L{lec_num_full}"
        work_dir = base_work / course / dir_name

        return ParsedLectureInfo(
            course=course,
            date=date_str,
            time_str=time_str,
            lecture_num=lec_num_full,
            part=part,
            is_standard=True,
            original_filename=path.name,
            source_path=path.resolve(),
            work_dir=work_dir.resolve(),
            warning=None,
        )

    # 3. Try SONY recorder native pattern (e.g. 261015_0930 -> 20261015)
    match_sony = SONY_RECORDER_PATTERN.match(cleaned_stem)
    if match_sony:
        yy = match_sony.group("yy")
        mm = match_sony.group("mm")
        dd = match_sony.group("dd")
        date_str = f"20{yy}{mm}{dd}"
        time_str = match_sony.group("time")

        matched = match_course_from_timetable(date_str, time_str)
        if matched:
            course = matched
            is_std = True
            warning = f"依課表時間對齊（{time_str[:2]}:{time_str[2:]}），自動辨識為【{course}】。"
        else:
            course = "Unsorted"
            is_std = False
            warning = (
                f"檔案 '{path.name}' 符合錄音筆原生命名格式 (YYMMDD_HHMM)。"
                f"已自動識別日期為 {date_str}，課程暫歸類為 '{course}'。"
            )

        work_dir = base_work / course / date_str
        return ParsedLectureInfo(
            course=course,
            date=date_str,
            time_str=time_str,
            is_standard=is_std,
            original_filename=path.name,
            source_path=path.resolve(),
            work_dir=work_dir.resolve(),
            warning=warning,
        )

    # 4. Fallback: check if an 8-digit date is embedded anywhere in the filename
    match_date = DATE_DETECTOR_PATTERN.search(cleaned_stem)
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
    is_generic = (
        not cleaned_stem
        or cleaned_stem.lower() in GENERIC_AUDIO_NAMES
        or bool(re.match(r"^(rec|track|audio|sound|voice)?\d*$", cleaned_stem.lower()))
    )
    if is_generic:
        course = "Unsorted"
        warning = (
            f"檔案 '{path.name}' 無法辨識課程與日期，已依檔案修改時間設定為 "
            f"{date_str}，課程暫歸類為 '{course}'。"
        )
    else:
        # User supplied a meaningful course/topic name without date (e.g. '畢業流程說明')
        course = cleaned_stem
        warning = (
            f"檔案 '{path.name}' 未包含日期或講數，已識別主題為 '{course}'，"
            f"並依檔案修改時間設定日期為 {date_str}。"
        )

    work_dir = base_work / course / date_str

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
