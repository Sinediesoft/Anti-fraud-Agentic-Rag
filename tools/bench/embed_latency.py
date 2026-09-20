"""嵌入模型延遲實測（S12）。

量兩件事，因為嵌入模型有兩種成本天差地別的用法：
  1. 查詢時編碼一句話 —— 在 S12 的 1 秒延遲預算裡，這是關鍵路徑
  2. 建索引時批次編碼幾千筆 —— 離線，看吞吐不看延遲

條件對齊 docs/model-lock.md 的跨平台決議：device="cpu"、dtype=float32。

用法（不要動專案相依）：
    uv run --no-project --python 3.11 --with torch --with transformers \
        --with sentencepiece python tools/bench/embed_latency.py
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _corpus import QUERIES, make_docs  # noqa: E402

# 小的排前面：bge-m3 的下載在 C 的機器上卡過兩次，先確保拿得到一組數字
MODELS = [
    ("text2vec-base-chinese", "shibing624/text2vec-base-chinese", "mean"),
    ("bge-m3", "BAAI/bge-m3", "cls"),
]

BUDGET_MS = 1000  # S12 的查詢延遲門檻


def pool(out, mask, how: str):
    """bge 系列取 CLS，text2vec 取 mean。用錯速度差不多，但向量會是垃圾。"""
    h = out.last_hidden_state
    if how == "cls":
        return F.normalize(h[:, 0], p=2, dim=1)
    m = mask.unsqueeze(-1).expand(h.size()).float()
    return F.normalize((h * m).sum(1) / m.sum(1).clamp(min=1e-9), p=2, dim=1)


def encode(tok, model, texts, how, batch, max_len=512):
    with torch.inference_mode():
        for i in range(0, len(texts), batch):
            enc = tok(
                texts[i : i + batch],
                padding=True,
                truncation=True,
                max_length=max_len,
                return_tensors="pt",
            )
            pool(model(**enc), enc["attention_mask"], how)


def main() -> None:
    print(f"torch {torch.__version__} / 執行緒 {torch.get_num_threads()}")
    print("條件：device=cpu, dtype=float32（本專案的跨平台決議）")
    print()

    docs = make_docs(64, paragraphs_each=5)

    for label, repo, how in MODELS:
        print("=" * 60)
        print(f"[*] {label}")
        print("=" * 60)

        # 一個模型下載失敗不該拖垮另一個
        try:
            t0 = time.perf_counter()
            tok = AutoTokenizer.from_pretrained(repo)
            model = AutoModel.from_pretrained(repo, dtype=torch.float32)
            model.eval()
            load = time.perf_counter() - t0
        except Exception as e:  # noqa: BLE001 —— 量測腳本，要的是繼續跑完另一個
            print(f"[X] 載入失敗，跳過：{type(e).__name__}: {str(e)[:160]}")
            print()
            continue

        params = sum(p.numel() for p in model.parameters())
        qtok = statistics.mean(len(tok(q)["input_ids"]) for q in QUERIES)
        dtok = statistics.mean(
            len(tok(d, truncation=True, max_length=512)["input_ids"]) for d in docs[:8]
        )
        print(f"參數 {params / 1e6:.0f}M / 向量維度 {model.config.hidden_size} / 載入 {load:.1f}s")
        print(f"查詢平均 {qtok:.0f} token / 文件平均 {dtok:.0f} token")
        print()

        # 1) 查詢延遲：一次一句，這是 S12 關鍵路徑
        encode(tok, model, QUERIES[:2], how, 1)  # 暖身
        runs = []
        for _ in range(4):
            for q in QUERIES:
                t = time.perf_counter()
                encode(tok, model, [q], how, 1)
                runs.append((time.perf_counter() - t) * 1000)
        runs.sort()
        p50 = statistics.median(runs)
        p95 = runs[int(len(runs) * 0.95) - 1]
        print(f"查詢編碼（單句）   p50 {p50:6.0f}ms   p95 {p95:6.0f}ms   最慢 {max(runs):6.0f}ms")
        print(f"                   佔 S12 的 1 秒預算 {p50 / BUDGET_MS * 100:.0f}%")

        # 2) 建索引吞吐：批次編碼長文件
        encode(tok, model, docs[:4], how, 8)  # 暖身
        t = time.perf_counter()
        encode(tok, model, docs, how, 8)
        rate = len(docs) / (time.perf_counter() - t)
        print(f"建索引吞吐         {rate:.2f} 筆/秒（batch=8, {dtok:.0f} token）")
        for n in (1000, 3000, 5000):
            print(f"                   {n} 筆需 {n / rate / 60:.0f} 分鐘")
        print()


if __name__ == "__main__":
    main()
