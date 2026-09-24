"""算自己的分數（make eval MODULE=…）。

所有指標一律呼叫 shared.eval，不要自己寫 —— 算法不同分數就不能比。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml
from contracts import AnalyzeInput
from shared import eval as shared_eval

from .module import build_module

MODULE_DIR = Path(__file__).resolve().parent
QUESTIONS = MODULE_DIR / "eval" / "route_questions.yaml"
JUDGEMENTS = MODULE_DIR / "eval" / "retrieval_judgements.json"


def _relevant_from_judgements() -> dict[int, list[str]]:
    """讀人工判定的相關案例（S12，2026-09-23）。

    route_questions 每題只標了一筆 gold —— 那題是從哪一筆案例改寫來的。
    但語料 9,167 筆裡 71.5% 是同一個 label，一句查詢必然對應到幾十筆同樣切題
    的案例，拿「有沒有撈回改寫來源那一筆」當 Recall，量到的是低估值：
    撈到另一筆一樣切題的案例會被算成失敗。

    所以另外做了一輪相關性判定（`make_retrieval_judgements.py` 產生判定頁，
    池 = Top-5 ∪ gold，共 106 筆），把每題**所有**被判為相關的案例當正確答案。
    這是 IR 的標準做法（pooling + relevance judgement），指標本身仍然走
    shared.eval.recall_at_k，只是餵給它的 relevant 從一筆變成一組。

    判定檔不存在就回空的，呼叫端會退回單一 gold。
    """
    if not JUDGEMENTS.exists():
        return {}
    raw = json.loads(JUDGEMENTS.read_text(encoding="utf-8")).get("judgements", {})
    out: dict[int, list[str]] = {}
    for key, is_relevant in raw.items():
        if not is_relevant:
            continue
        qi, _, case_id = key.partition("|")
        out.setdefault(int(qi), []).append(case_id)
    return out


def run() -> dict[str, float]:
    module = build_module()
    scores: dict[str, float] = {}

    if QUESTIONS.exists():
        questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8")) or []
        if questions:
            # S14 第 3 點：自己的 20 題，can_handle 至少 17 題要高於門檻
            hits = 0
            latencies: list[float] = []
            for q in questions:
                started = time.perf_counter()
                score = module.can_handle(AnalyzeInput(text=q.get("text", "")))
                latencies.append((time.perf_counter() - started) * 1000)
                if score >= module.pack.thresholds.route_min:
                    hits += 1
            scores["can_handle_hit"] = hits / len(questions)
            scores["latency_p95_s"] = shared_eval.latency_p95(latencies) / 1000

            # S12：Recall@5 —— 需要每題標出正確答案的案例編號
            judged = _relevant_from_judgements()
            retrieved: list[list[str]] = []
            relevant: list[list[str]] = []
            gold_only: list[list[str]] = []
            for qi, q in enumerate(questions):
                gold = list(q.get("gold_case_ids") or [])
                if not gold:
                    continue
                verdict = module.analyze(AnalyzeInput(text=q.get("text", "")))
                retrieved.append([c.case_id for c in verdict.similar_cases])
                gold_only.append(gold)
                # 判定過的題目用整組相關案例，沒判定過的退回單一 gold
                relevant.append(judged.get(qi) or gold)

            if retrieved:
                scores["recall_at_5"] = shared_eval.recall_at_k(retrieved, relevant, k=5)
                scores["mrr_at_10"] = shared_eval.mrr_at_k(retrieved, relevant, k=10)
                if judged:
                    # 兩個數字一起報，才看得出判定讓結果差多少。
                    # gold_only 是低估值，留著當對照，不是拿來當成績的。
                    scores["recall_at_5_gold_only"] = shared_eval.recall_at_k(
                        retrieved, gold_only, k=5
                    )

    rows = shared_eval.score_table(scores)
    for row in rows:
        mark = "[OK]" if row.passed else "[X]"
        arrow = ">=" if row.direction == "ge" else "<="
        print(f"  {mark} {row.metric:<18} {row.value:.3f}  （門檻 {arrow} {row.threshold}）")
    return scores
