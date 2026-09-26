"""算自己的分數（make eval MODULE=…）。

所有指標一律呼叫 shared.eval，不要自己寫 —— 算法不同分數就不能比。
"""

from __future__ import annotations

import time
from pathlib import Path

import yaml
from contracts import AnalyzeInput
from shared import eval as shared_eval

from .module import build_module

MODULE_DIR = Path(__file__).resolve().parent
QUESTIONS = MODULE_DIR / "eval" / "route_questions.yaml"


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
            labelled = [q for q in questions if q.get("gold_case_ids")]
            if labelled:
                retrieved = []
                relevant = []
                for q in labelled:
                    verdict = module.analyze(AnalyzeInput(text=q.get("text", "")))
                    retrieved.append([c.case_id for c in verdict.similar_cases])
                    relevant.append(list(q["gold_case_ids"]))
                scores["recall_at_5"] = shared_eval.recall_at_k(retrieved, relevant, k=5)
                scores["mrr_at_10"] = shared_eval.mrr_at_k(retrieved, relevant, k=10)

    rows = shared_eval.score_table(scores)
    for row in rows:
        mark = "[OK]" if row.passed else "[X]"
        arrow = ">=" if row.direction == "ge" else "<="
        print(f"  {mark} {row.metric:<18} {row.value:.3f}  （門檻 {arrow} {row.threshold}）")
    return scores
