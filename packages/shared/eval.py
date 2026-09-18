"""分數計算 —— 共用三樣之一，不准自己寫（說明書 S5 第 3 點）。

五個人算分數都呼叫這裡，算法一致分數才能比。
刻意用純 Python 實作，不依賴 scikit-learn：少一個安裝失敗的理由，
而且五台電腦算出來的數字保證一模一樣。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import mean

# ──────────────────────────────────────────────────────────────
# 檢索（S12：Recall@5 ≥ 0.75、p95 ≤ 1 秒）
# ──────────────────────────────────────────────────────────────


def recall_at_k(retrieved: list[list[str]], relevant: list[list[str]], k: int = 5) -> float:
    """前 k 筆結果裡有沒有包含正確答案。

    retrieved[i] 是第 i 題檢索回來的案例編號（照排名），relevant[i] 是正確答案。
    """
    if not retrieved:
        return 0.0
    hits = 0
    for got, want in zip(retrieved, relevant, strict=True):
        if set(got[:k]) & set(want):
            hits += 1
    return hits / len(retrieved)


def mrr_at_k(retrieved: list[list[str]], relevant: list[list[str]], k: int = 10) -> float:
    """正確答案平均排在第幾名的倒數。越接近 1 代表越常排第一。"""
    if not retrieved:
        return 0.0
    total = 0.0
    for got, want in zip(retrieved, relevant, strict=True):
        want_set = set(want)
        for rank, case_id in enumerate(got[:k], start=1):
            if case_id in want_set:
                total += 1.0 / rank
                break
    return total / len(retrieved)


def latency_p95(samples_ms: list[float]) -> float:
    """一百次查詢中最慢的那五次以外，其他都在這個時間內完成。"""
    if not samples_ms:
        return 0.0
    ordered = sorted(samples_ms)
    idx = max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered))) - 1))
    return ordered[idx]


# ──────────────────────────────────────────────────────────────
# 分類（S13：macro-F1 比對照組相對進步 ≥ 10%）
# ──────────────────────────────────────────────────────────────


@dataclass
class ClassificationReport:
    macro_f1: float
    accuracy: float
    per_label: dict[str, dict[str, float]] = field(default_factory=dict)
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)


def macro_f1(y_true: list[str], y_pred: list[str]) -> float:
    """大類小類一樣重。用一般準確率在這種資料上會完全失真。"""
    return classification_report(y_true, y_pred).macro_f1


def classification_report(y_true: list[str], y_pred: list[str]) -> ClassificationReport:
    """連混淆矩陣一起回傳 —— S18 調路由時要靠它看誰常被認成誰。"""
    if len(y_true) != len(y_pred):
        raise ValueError("y_true 與 y_pred 長度不一致")
    if not y_true:
        return ClassificationReport(macro_f1=0.0, accuracy=0.0)

    labels = sorted(set(y_true) | set(y_pred))
    confusion: dict[tuple[str, str], int] = Counter(zip(y_true, y_pred, strict=True))
    per_label: dict[str, dict[str, float]] = {}

    f1s: list[float] = []
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        # 正確答案裡完全沒出現的類別不計入平均，否則憑空多一個 0 把分數壓下來
        if tp + fn > 0:
            f1s.append(f1)
        per_label[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(tp + fn),
        }

    accuracy = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == p) / len(y_true)
    return ClassificationReport(
        macro_f1=mean(f1s) if f1s else 0.0,
        accuracy=accuracy,
        per_label=per_label,
        confusion=dict(confusion),
    )


def relative_gain(baseline: float, candidate: float) -> float:
    """比對照組相對進步多少。S13 要求 ≥ 0.10。"""
    if baseline <= 0:
        return 0.0
    return (candidate - baseline) / baseline


# ──────────────────────────────────────────────────────────────
# 標註一致性（S10：kappa ≥ 0.70）
# ──────────────────────────────────────────────────────────────


def cohen_kappa(rater_a: list[str], rater_b: list[str]) -> float:
    """兩個人標同一批資料，意見一致的程度（扣掉瞎猜也會一致的部分）。

    太低代表標註規則寫得不清楚，不是人不認真 —— 先改說明，再重標。
    """
    if len(rater_a) != len(rater_b):
        raise ValueError("兩位標註者的筆數不一致")
    n = len(rater_a)
    if n == 0:
        return 0.0

    observed = sum(1 for a, b in zip(rater_a, rater_b, strict=True) if a == b) / n
    count_a = Counter(rater_a)
    count_b = Counter(rater_b)
    expected = sum(
        (count_a[label] / n) * (count_b[label] / n) for label in set(count_a) | set(count_b)
    )
    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


# ──────────────────────────────────────────────────────────────
# 文字辨識（S11：CER ≤ 0.15）
# ──────────────────────────────────────────────────────────────


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    """認錯字的比例。0.15 代表每 100 個字大約錯 15 個。"""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return _edit_distance(reference, hypothesis) / len(reference)


def corpus_cer(references: list[str], hypotheses: list[str]) -> float:
    """整批算，不是每張算完再平均 —— 長短不一時平均會失真。"""
    if not references:
        return 0.0
    total_err = sum(_edit_distance(r, h) for r, h in zip(references, hypotheses, strict=True))
    total_len = sum(len(r) for r in references)
    return total_err / total_len if total_len else 0.0


# ──────────────────────────────────────────────────────────────
# 分數總表（S15 自評報告 / S20 三層總表）
# ──────────────────────────────────────────────────────────────

# 說明書附錄「完成檢查表」裡的門檻，全隊一致
THRESHOLDS: dict[str, tuple[float, str]] = {
    "deid_recall": (0.95, "ge"),
    "kappa": (0.70, "ge"),
    "ocr_cer": (0.15, "le"),
    "layout_accuracy": (0.85, "ge"),
    "recall_at_5": (0.75, "ge"),
    "latency_p95_s": (1.0, "le"),
    "macro_f1_gain": (0.10, "ge"),
    "field_f1_shared": (0.70, "ge"),
    "field_f1_specific": (0.60, "ge"),
    "route_accuracy": (0.85, "ge"),
    "can_handle_hit": (17 / 20, "ge"),
}


@dataclass
class ScoreRow:
    metric: str
    value: float
    threshold: float
    direction: str
    model_version: str = "尚未鎖定"
    measured_on: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.value >= self.threshold if self.direction == "ge" else self.value <= self.threshold
        )


def score_table(
    values: dict[str, float], *, model_version: str = "", measured_on: str = ""
) -> list[ScoreRow]:
    """把實測值跟門檻並排。沒達標的要寫清楚差多少（S15 第 5 點）。"""
    rows: list[ScoreRow] = []
    for metric, value in values.items():
        threshold, direction = THRESHOLDS.get(metric, (0.0, "ge"))
        rows.append(
            ScoreRow(
                metric=metric,
                value=value,
                threshold=threshold,
                direction=direction,
                model_version=model_version or "尚未鎖定",
                measured_on=measured_on,
            )
        )
    return rows
