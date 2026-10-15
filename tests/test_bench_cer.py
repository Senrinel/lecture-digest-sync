from lecture_pipeline.bench.cer import compute_cer, normalize_chinese_text_for_cer


def test_normalize_chinese_text():
    raw = "大家好，今天我們來討論 Python 的演算法（Algorithm）！"
    norm = normalize_chinese_text_for_cer(raw)
    assert norm == "大家好今天我們來討論python的演算法algorithm"


def test_compute_cer_exact_match():
    ref = "這是一堂機器學習課程"
    hyp = "這是一堂機器學習課程"
    res = compute_cer(ref, hyp)
    assert res.cer == 0.0
    assert res.substitutions == 0
    assert res.deletions == 0
    assert res.insertions == 0


def test_compute_cer_with_errors_and_glossary():
    ref = "我們使用 Docker 與 Kubernetes 部署"
    # hyp has 1 substitution: 步署 -> 部署, 1 deletion: 與
    hyp = "我們使用 Docker Kubernetes 步署"
    glossary = ["Docker", "Kubernetes"]

    res = compute_cer(ref, hyp, glossary=glossary)
    assert res.cer > 0.0
    assert res.term_recall == 1.0  # Both Docker and Kubernetes are found
