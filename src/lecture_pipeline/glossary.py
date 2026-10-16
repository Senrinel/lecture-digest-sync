"""Course glossary and pure-local technical vocabulary extraction module.

Operates 100% locally with 0 cloud API calls, extracting technical terms,
acronyms, algorithms, and bilingual definitions from PDF slides/lecture notes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Set


# Common English non-domain stopwords that might be capitalized in slide titles
ENGLISH_SLIDE_STOPWORDS = {
    "Chapter", "Section", "Lecture", "Course", "Outline", "Overview", "Summary",
    "Introduction", "Conclusion", "Example", "Exercise", "Homework", "Project",
    "Week", "Part", "Review", "Problem", "Solution", "Question", "Answer",
    "Reference", "References", "Appendix", "Figure", "Table", "Page", "Index",
    "Today", "Tomorrow", "Next", "First", "Second", "Third", "Final", "Midterm",
    "Department", "University", "College", "School", "Professor", "Instructor",
    "Student", "Notice", "Agenda", "Schedule", "Office", "Hours", "Contact",
    "Email", "Name", "Date", "Slide", "Slides", "Version", "All", "Rights", "Reserved"
}

# Academic and technical Chinese compound suffixes
CHINESE_TECH_SUFFIXES = (
    "演算法", "定理", "矩陣", "架構", "函數", "變數", "模型", "協議",
    "指標", "方程", "方程式", "維度", "空間", "圖形", "微積分", "統計學",
    "拓撲", "特徵值", "特徵向量", "殘差", "回歸", "神經網路", "分類器",
    "聚類", "優化", "最佳化", "轉換", "變換", "映射", "複數", "張量"
)


def load_course_glossary(
    course: str,
    courses_dir: Path | str = "courses",
) -> List[str]:
    """Load technical terminology and proper nouns for a given course.

    Looks for `courses/<course>/glossary.txt`.
    File format: one term per line. Lines starting with '#' are ignored.

    Args:
        course: Name of the course (e.g. 'DataMining', 'Machine-Learning').
        courses_dir: Base directory for course metadata.

    Returns:
        List of distinct glossary phrases (stripped, non-empty).
    """
    course_folder = Path(courses_dir) / course
    glossary_file = course_folder / "glossary.txt"

    if not glossary_file.is_file():
        return []

    terms: List[str] = []
    seen = set()

    with open(glossary_file, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = line.split("#", 1)[0].strip()
            if not cleaned:
                continue
            if cleaned not in seen:
                seen.add(cleaned)
                terms.append(cleaned)

    return terms


def extract_terms_from_text(text: str) -> List[str]:
    """Extract domain terms, technical acronyms, and bilingual terms from text without API calls.

    Pure local extraction using regular expressions and statistical patterns.
    """
    if not text:
        return []

    found_terms: List[str] = []
    seen: Set[str] = set()

    def add_term(term: str):
        t = term.strip().strip(",.:;\"'()[]{}")
        if not t or len(t) < 2 or len(t) > 50:
            return
        if t in seen or t in ENGLISH_SLIDE_STOPWORDS:
            return
        # Skip pure numbers or dates
        if re.match(r"^[\d\.\-\/\:]+$", t):
            return
        seen.add(t)
        found_terms.append(t)

    # 1. Bilingual pairs: 中文 (English) or English (中文)
    bilingual_pattern_1 = re.findall(r"([\u4e00-\u9fa5]{2,12})\s*[\(（]([A-Za-z0-9\s\-\_]{2,35})[\)）]", text)
    for zh, en in bilingual_pattern_1:
        add_term(zh)
        add_term(en)

    bilingual_pattern_2 = re.findall(r"([A-Za-z0-9\s\-\_]{2,35})\s*[\(（]([\u4e00-\u9fa5]{2,12})[\)）]", text)
    for en, zh in bilingual_pattern_2:
        add_term(en)
        add_term(zh)

    # 2. Uppercase technical acronyms (2 to 7 characters, e.g. ASR, VRAM, FFT, DFS, BFS, LUFS)
    acronyms = re.findall(r"\b[A-Z0-9]{2,7}\b", text)
    for ac in acronyms:
        # Avoid Roman numerals I, II, III, IV, VI, etc.
        if re.match(r"^[IVXLCDM]+$", ac) and len(ac) <= 4:
            continue
        add_term(ac)

    # 3. Capitalized multi-word technical concepts (e.g. Dynamic Programming, Gradient Descent, Dijkstra's Algorithm)
    concept_pattern = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+(?:'s\s+[A-Z][a-z]+)?)\b", text)
    for concept in concept_pattern:
        words = concept.split()
        if any(w in ENGLISH_SLIDE_STOPWORDS for w in words):
            continue
        add_term(concept)

    # 4. Chinese technical terms with academic suffixes or domain terms
    tech_pattern = rf"([\u4e00-\u9fa5]{{0,6}}(?:{'|'.join(CHINESE_TECH_SUFFIXES)}))"
    for m in re.findall(tech_pattern, text):
        if len(m) >= 2:
            add_term(m)

    return found_terms


def extract_terms_from_pdf(pdf_path: Path | str) -> List[str]:
    """Extract text and domain terms from a PDF slide or lecture note."""
    p = Path(pdf_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"PDF file not found: {p}")

    import pypdf

    full_text_parts = []
    with open(p, "rb") as f:
        reader = pypdf.PdfReader(f)
        for page in reader.pages:
            try:
                page_text = page.extract_text()
                if page_text:
                    full_text_parts.append(page_text)
            except Exception:
                continue

    combined_text = "\n".join(full_text_parts)
    return extract_terms_from_text(combined_text)


def extract_terms_from_file(file_path: Path | str) -> List[str]:
    """Extract terms from PDF, Markdown, or TXT file."""
    p = Path(file_path).resolve()
    if p.suffix.lower() == ".pdf":
        return extract_terms_from_pdf(p)
    elif p.suffix.lower() in [".txt", ".md"]:
        text = p.read_text(encoding="utf-8", errors="ignore")
        return extract_terms_from_text(text)
    else:
        # Attempt text read
        text = p.read_text(encoding="utf-8", errors="ignore")
        return extract_terms_from_text(text)


def update_course_glossary(
    course: str,
    new_terms: Iterable[str],
    courses_dir: Path | str = "courses",
) -> Path:
    """Merge newly extracted terms into courses/<course>/glossary.txt preserving existing terms."""
    course_dir = Path(courses_dir) / course
    course_dir.mkdir(parents=True, exist_ok=True)
    glossary_path = course_dir / "glossary.txt"

    existing_terms = set(load_course_glossary(course, courses_dir=courses_dir))
    to_add = [t for t in new_terms if t not in existing_terms]

    if not to_add:
        return glossary_path

    with open(glossary_path, "a", encoding="utf-8") as f:
        if glossary_path.stat().st_size == 0:
            f.write(f"# Course glossary for {course}\n")
        f.write(f"\n# --- Auto-extracted terms ({len(to_add)}) ---\n")
        for term in to_add:
            f.write(f"{term}\n")

    return glossary_path


def auto_extract_course_glossary(
    course: str,
    source_path: Path | str,
    courses_dir: Path | str = "courses",
) -> List[str]:
    """High-level function: extract terms from file/folder and update course glossary."""
    src = Path(source_path).resolve()
    all_extracted: List[str] = []

    if src.is_file():
        all_extracted.extend(extract_terms_from_file(src))
    elif src.is_dir():
        for f in src.iterdir():
            if f.is_file() and f.suffix.lower() in [".pdf", ".txt", ".md"]:
                all_extracted.extend(extract_terms_from_file(f))

    # Deduplicate while preserving order
    unique_terms = []
    seen = set()
    for t in all_extracted:
        if t not in seen:
            seen.add(t)
            unique_terms.append(t)

    update_course_glossary(course, unique_terms, courses_dir=courses_dir)
    return unique_terms
