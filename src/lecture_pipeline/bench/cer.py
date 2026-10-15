"""Evaluation metrics calculation for ASR transcripts (CER, Term Recall, RTF).

Provides standardized text normalization (stripping punctuation, converting full-width
characters, lowercasing Latin terms) and character-level edit distance calculation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Tuple


def normalize_text_for_cer(text: str) -> str:
    """Normalize text for fair character error rate comparison.

    1. NFKC Unicode normalization (converts full-width English/digits to ASCII).
    2. Removes all Chinese and Western punctuation symbols.
    3. Strips all whitespace characters.
    4. Lowercases Latin alphabets.
    """
    if not text:
        return ""

    # 1. NFKC normalization
    normalized = unicodedata.normalize("NFKC", text)

    # 2. Lowercase
    normalized = normalized.lower()

    # 3. Strip punctuation and whitespace: keep only alphanumeric and CJK Unified Ideographs
    # CJK range: \u4e00-\u9fff, \u3400-\u4dbf, etc.
    cleaned = re.sub(
        r"[^\w\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]",
        "",
        normalized,
        flags=re.UNICODE,
    )
    # Replace underscores that \w might retain
    cleaned = cleaned.replace("_", "")
    return cleaned


def calculate_levenshtein(ref: str, hyp: str) -> Tuple[int, int, int, int]:
    """Compute character-level substitutions, deletions, and insertions.

    Returns:
        (substitutions, deletions, insertions, total_edits)
    """
    n, m = len(ref), len(hyp)
    # dp[i][j] stores (cost, S, D, I)
    dp = [[(0, 0, 0, 0) for _ in range(m + 1)] for _ in range(n + 1)]

    for i in range(1, n + 1):
        dp[i][0] = (i, 0, i, 0)  # i deletions
    for j in range(1, m + 1):
        dp[0][j] = (j, 0, 0, j)  # j insertions

    for i in range(1, n + 1):
        r_char = ref[i - 1]
        for j in range(1, m + 1):
            h_char = hyp[j - 1]
            if r_char == h_char:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                # Options: Substitution, Deletion, Insertion
                sub_cost, s_s, s_d, s_i = dp[i - 1][j - 1]
                del_cost, d_s, d_d, d_i = dp[i - 1][j]
                ins_cost, i_s, i_d, i_i = dp[i][j - 1]

                best_cost = min(sub_cost + 1, del_cost + 1, ins_cost + 1)
                if best_cost == sub_cost + 1:
                    dp[i][j] = (best_cost, s_s + 1, s_d, s_i)
                elif best_cost == del_cost + 1:
                    dp[i][j] = (best_cost, d_s, d_d + 1, d_i)
                else:
                    dp[i][j] = (best_cost, i_s, i_d, i_i + 1)

    cost, s, d, ins = dp[n][m]
    return s, d, ins, cost


@dataclass
class CERResult:
    """Character Error Rate evaluation metrics."""

    reference_length: int
    hypothesis_length: int
    substitutions: int
    deletions: int
    insertions: int
    total_errors: int
    cer: float  # total_errors / reference_length (can exceed 1.0)
    accuracy: float  # max(0.0, 1.0 - cer)


def compute_cer(reference_text: str, hypothesis_text: str) -> CERResult:
    """Compute CER between ground-truth reference and candidate transcript.

    Args:
        reference_text: Golden reference transcript text.
        hypothesis_text: ASR candidate transcript text.

    Returns:
        CERResult with error counts, CER ratio, and accuracy.
    """
    norm_ref = normalize_text_for_cer(reference_text)
    norm_hyp = normalize_text_for_cer(hypothesis_text)

    ref_len = len(norm_ref)
    hyp_len = len(norm_hyp)

    if ref_len == 0:
        cer = 0.0 if hyp_len == 0 else 1.0
        return CERResult(
            reference_length=0,
            hypothesis_length=hyp_len,
            substitutions=0,
            deletions=0,
            insertions=hyp_len,
            total_errors=hyp_len,
            cer=cer,
            accuracy=1.0 if cer == 0.0 else 0.0,
        )

    s, d, ins, total = calculate_levenshtein(norm_ref, norm_hyp)
    cer = round(total / ref_len, 4)
    accuracy = round(max(0.0, 1.0 - cer), 4)

    return CERResult(
        reference_length=ref_len,
        hypothesis_length=hyp_len,
        substitutions=s,
        deletions=d,
        insertions=ins,
        total_errors=total,
        cer=cer,
        accuracy=accuracy,
    )


@dataclass
class TermRecallResult:
    """Domain term recall metrics."""

    total_terms: int
    matched_count: int
    recall_rate: float
    matched_terms: List[str]
    missing_terms: List[str]


def compute_term_recall(transcript_text: str, terms: List[str]) -> TermRecallResult:
    """Evaluate recall rate of domain-specific vocabulary terms in transcript."""
    if not terms:
        return TermRecallResult(
            total_terms=0,
            matched_count=0,
            recall_rate=1.0,
            matched_terms=[],
            missing_terms=[],
        )

    norm_text = normalize_text_for_cer(transcript_text)
    matched = []
    missing = []

    for term in terms:
        norm_term = normalize_text_for_cer(term)
        if norm_term and norm_term in norm_text:
            matched.append(term)
        else:
            missing.append(term)

    matched_cnt = len(matched)
    total_cnt = len(terms)
    rate = round(matched_cnt / max(1, total_cnt), 4)

    return TermRecallResult(
        total_terms=total_cnt,
        matched_count=matched_cnt,
        recall_rate=rate,
        matched_terms=matched,
        missing_terms=missing,
    )
