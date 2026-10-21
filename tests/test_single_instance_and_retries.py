"""Test suite for single-instance guard, 503 retry strategy, and local NLP glossary extraction."""

import sys
from pathlib import Path
import pytest

from lecture_pipeline.automation.quota import (
    is_temporary_service_error,
    is_quota_exhausted_error,
    is_api_or_quota_error,
    QuotaManager,
)
from lecture_pipeline.glossary import extract_terms_from_text


def test_temporary_service_error_classification():
    """Verify 503, connection drops, and server overloads are correctly identified as temporary."""
    e_503 = RuntimeError("503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.'}}")
    e_timeout = TimeoutError("The read operation timed out")
    e_conn = ConnectionResetError("Remote server disconnected / broken pipe")
    e_502 = Exception("502 Bad Gateway from upstream proxy")

    assert is_temporary_service_error(e_503) is True
    assert is_temporary_service_error(e_timeout) is True
    assert is_temporary_service_error(e_conn) is True
    assert is_temporary_service_error(e_502) is True

    # Quota errors must NOT be marked as temporary service errors
    e_429 = RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota")
    assert is_temporary_service_error(e_429) is False


def test_quota_exhausted_error_classification():
    """Verify 429 and RESOURCE_EXHAUSTED daily limit errors are distinguished."""
    e_429 = RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota, please check your plan and billing details.")
    e_rpd = Exception("Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20")
    
    assert is_quota_exhausted_error(e_429) is True
    assert is_quota_exhausted_error(e_rpd) is True

    # 503 must NOT be marked as quota exhausted
    e_503 = RuntimeError("503 UNAVAILABLE: Spikes in demand are usually temporary")
    assert is_quota_exhausted_error(e_503) is False


def test_is_api_or_quota_error():
    """Verify is_api_or_quota_error detects both 503 and 429."""
    e_503 = RuntimeError("503 UNAVAILABLE")
    e_429 = RuntimeError("429 RESOURCE_EXHAUSTED")
    e_value = ValueError("Invalid parameter value")

    assert is_api_or_quota_error(e_503) is True
    assert is_api_or_quota_error(e_429) is True
    assert is_api_or_quota_error(e_value) is False


def test_jieba_nlp_glossary_extraction():
    """Verify local NLP (jieba + regex) extracts technical terms and filters conversational noise."""
    text = """
    今天我們介紹深度學習 (Deep Learning) 與卷積神經網絡 (Convolutional Neural Networks, CNN)。
    在反向傳播 (Backpropagation) 中，我們計算損失函數 (Loss Function) 對權重矩陣的梯度。
    本堂課會探討特徵向量 (Eigenvectors) 與奇異值分解 (Singular Value Decomposition, SVD)。
    請注意下週會考傅立葉轉換 (Fourier Transform) 與線性代數的基本定理。
    """
    terms = extract_terms_from_text(text)
    
    # Check that key domain terms are captured
    assert "深度學習" in terms
    assert "Deep Learning" in terms
    assert "反向傳播" in terms
    assert "Backpropagation" in terms
    assert "特徵向量" in terms
    assert "Eigenvectors" in terms
    assert "傅立葉轉換" in terms
    assert "Fourier Transform" in terms
    assert "CNN" in terms
    assert "SVD" in terms
    assert "線性代數" in terms
    assert "奇異值分解" in terms or "奇異值" in terms

    # Check that spoken noise is not included as whole terms
    assert "今天我們介紹" not in terms
    assert "本堂課會探討" not in terms
    assert "請注意下週會考" not in terms


def test_quota_lock_and_unlock(tmp_path):
    """Verify QuotaManager creates and releases lock files properly."""
    lock_file = tmp_path / "test_quota_lock.json"
    qm = QuotaManager(lock_file=lock_file)
    
    # Initially not locked
    is_locked, until = qm.is_quota_locked()
    assert is_locked is False
    assert until is None

    # Mark quota exhausted
    reset_dt = qm.mark_quota_exhausted(service="test", reason="429 Limit")
    assert lock_file.is_file()
    
    is_locked, until = qm.is_quota_locked()
    assert is_locked is True
    assert until is not None

    # Unlock
    qm.unlock_quota()
    is_locked, until = qm.is_quota_locked()
    assert is_locked is False
    assert not lock_file.is_file()
