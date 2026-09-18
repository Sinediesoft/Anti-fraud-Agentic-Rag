"""分數計算的測試。五個人算分數都走這裡，算錯了五份報告一起錯。"""

from __future__ import annotations

from shared import eval as se


def test_recall_at_5():
    retrieved = [["a", "b", "c"], ["x", "y", "z"]]
    relevant = [["c"], ["w"]]
    assert se.recall_at_k(retrieved, relevant, k=5) == 0.5


def test_recall_受_k_限制():
    assert se.recall_at_k([["a", "b", "c"]], [["c"]], k=2) == 0.0


def test_mrr():
    assert se.mrr_at_k([["a", "b"]], [["b"]], k=10) == 0.5


def test_kappa_完全一致為一():
    assert se.cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0


def test_kappa_扣掉瞎猜也會一致的部分():
    # 兩個人都只標同一個類別時，表面一致率 100% 但 kappa 應該是 1（無變異的特例）
    assert se.cohen_kappa(["a", "a"], ["a", "a"]) == 1.0
    # 完全相反
    assert se.cohen_kappa(["a", "b"], ["b", "a"]) < 0


def test_macro_f1_大類小類一樣重():
    y_true = ["a", "a", "a", "b"]
    y_pred = ["a", "a", "a", "a"]
    # 準確率 0.75 看起來不錯，但 b 完全沒抓到，macro-F1 會誠實反映
    report = se.classification_report(y_true, y_pred)
    assert report.accuracy == 0.75
    assert report.macro_f1 < 0.5


def test_混淆矩陣有記錄():
    report = se.classification_report(["a", "b"], ["a", "a"])
    assert report.confusion[("b", "a")] == 1


def test_相對進步():
    assert abs(se.relative_gain(0.50, 0.55) - 0.10) < 1e-9


def test_字元錯誤率():
    assert se.cer("台北市", "台北市") == 0.0
    assert se.cer("台北市", "臺北市") == 1 / 3


def test_整批算_cer_不是每張算完再平均():
    refs = ["一二三四五六七八九十", "甲"]
    hyps = ["一二三四五六七八九十", "乙"]
    # 每張平均會是 0.5，整批算是 1/11
    assert abs(se.corpus_cer(refs, hyps) - 1 / 11) < 1e-9


def test_p95():
    assert se.latency_p95([float(i) for i in range(1, 101)]) == 95.0


def test_分數總表標出有沒有達標():
    rows = se.score_table({"recall_at_5": 0.80, "ocr_cer": 0.20})
    passed = {r.metric: r.passed for r in rows}
    assert passed["recall_at_5"] is True  # ≥ 0.75
    assert passed["ocr_cer"] is False  # ≤ 0.15
