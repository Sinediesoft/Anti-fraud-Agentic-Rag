"""重排序模型延遲實測（S12）。

S12 要求查詢延遲 p95 <= 1 秒，流程是「用重排序模型把前 50 筆重排」。
這支腳本量的就是那一段 —— 不含向量檢索，也不含 SLM 生成。

重排序是 cross-encoder：每一對 (query, document) 都要進模型算一次。
所以成本是「筆數 x 文件長度」，跟嵌入模型的單次編碼完全不同量級。

條件對齊 docs/model-lock.md 的跨平台決議：device="cpu"、dtype=float32。

用法（不要動專案相依）：
    uv run --no-project --python 3.11 --with torch --with transformers \
        --with sentencepiece python tools/bench/rerank_latency.py
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _corpus import QUERIES, make_docs  # noqa: E402

MODEL = "BAAI/bge-reranker-v2-m3"
QUERY = QUERIES[1]  # 假檢警那一句
ROUNDS = 3
BUDGET_MS = 1000


def bench(tok, model, docs, max_len=512, batch=8) -> float:
    pairs = [[QUERY, d] for d in docs]
    t0 = time.perf_counter()
    with torch.inference_mode():
        for i in range(0, len(pairs), batch):
            enc = tok(
                pairs[i : i + batch],
                padding=True,
                truncation=True,
                max_length=max_len,
                return_tensors="pt",
            )
            model(**enc).logits.view(-1).float()
    return (time.perf_counter() - t0) * 1000


def main() -> None:
    print(f"torch {torch.__version__} / 執行緒 {torch.get_num_threads()}")
    print("條件：device=cpu, dtype=float32（本專案的跨平台決議）")

    t0 = time.perf_counter()
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL, dtype=torch.float32)
    model.eval()
    params = sum(p.numel() for p in model.parameters())
    print(f"模型 {MODEL} / 參數 {params / 1e6:.0f}M / 載入 {time.perf_counter() - t0:.1f}s")
    print()

    header = f"{'文件長度':<14}{'筆數':>6}{'實際token':>11}{'p50':>10}{'最慢':>10}{'vs 門檻':>10}"
    print(header)
    print("-" * len(header))

    # 1 段約 60 token、2 段約 120、5 段約 290（接近真實 RAG 切塊）
    for label, paras in [("短", 1), ("中", 2), ("長（接近真實）", 5)]:
        for topk in (50, 20, 10):
            docs = make_docs(topk, paragraphs_each=paras)
            ntok = statistics.mean(len(tok(d)["input_ids"]) for d in docs)
            bench(tok, model, docs[:2])  # 暖身
            runs = [bench(tok, model, docs) for _ in range(ROUNDS)]
            p50 = statistics.median(runs)
            print(
                f"{label:<14}{topk:>6}{ntok:>11.0f}{p50:>8.0f}ms"
                f"{max(runs):>8.0f}ms{p50 / BUDGET_MS:>9.1f}x"
            )
        print()

    print("註：樣本數少，只看量級。門檻為 S12 的 1000ms，")
    print("    且這裡只算重排序 —— 實際查詢還要加上向量檢索與 SLM 生成。")


if __name__ == "__main__":
    main()
