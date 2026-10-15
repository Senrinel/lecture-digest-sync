"""CER (Character Error Rate) calculation and evaluation tools."""

import re
from dataclasses import dataclass
from typing import List, Set
import jiwer
from opencc import OpenCC

_opencc = OpenCC("s2tw")


@dataclass
class CERMetricResult:
    cer: float
    insertions: int
    deletions: int
    substitutions: int
    total_reference_chars: int
    term_recall: float  # Percentage of glossary terms correctly recognized


def normalize_chinese_text_for_cer(text: str) -> str:
    """Normalizes Chinese and English text for fair CER benchmark comparison.
    
    1. Converts to Taiwan Traditional Chinese (s2twp).
    2. Converts full-width alphanumeric to half-width.
    3. Converts English to lowercase.
    4. Strips all punctuation and whitespaces.
    """
    # 1. OpenCC conversion
    t = _opencc.convert(text)

    # 2. Full-width to half-width
    half = []
    for char in t:
        code = ord(char)
        if 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        elif code == 0x3000:
            code = 0x20
        half.append(chr(code))
    t = "".join(half)

    # 3. Lowercase English
    t = t.lower()

    # 4. Remove all punctuation and whitespace
    t = re.sub(r"[\s\.,\/#!$%\^&\*;:{}=\-_`~()?'\"“”‘’、，。！？；：…—（）《》〈〉【】]+", "", t)
    return t


def compute_cer(reference: str, hypothesis: str, glossary: List[str] = None) -> CERMetricResult:
    """Computes normalized Character Error Rate (CER) and glossary term recall."""
    norm_ref = normalize_chinese_text_for_cer(reference)
    norm_hyp = normalize_chinese_text_for_cer(hypothesis)

    if not norm_ref:
        return CERMetricResult(
            cer=0.0 if not norm_hyp else 1.0,
            insertions=len(norm_hyp),
            deletions=0,
            substitutions=0,
            total_reference_chars=0,
            term_recall=1.0,
        )

    # jiwer process_words can be used on character level by space-separating characters
    ref_chars = " ".join(list(norm_ref))
    hyp_chars = " ".join(list(norm_hyp))

    output = jiwer.process_words(ref_chars, hyp_chars)
    cer = output.wer  # Because words are single characters, WER equals CER
    insertions = output.insertions
    deletions = output.deletions
    substitutions = output.substitutions

    # Term recall
    term_recall = 1.0
    if glossary:
        found = 0
        total_terms = 0
        for term in glossary:
            norm_term = normalize_chinese_text_for_cer(term)
            if norm_term and norm_term in norm_ref:
                total_terms += 1
                if norm_term in norm_hyp:
                    found += 1
        if total_terms > 0:
            term_recall = round(found / total_terms, 4)

    return CERMetricResult(
        cer=round(cer, 4),
        insertions=insertions,
        deletions=deletions,
        substitutions=substitutions,
        total_reference_chars=len(norm_ref),
        term_recall=term_recall,
    )
