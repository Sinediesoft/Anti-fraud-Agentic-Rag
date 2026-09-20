# 模型延遲實測腳本（S3 / S12）

這裡的三支腳本產生了 `docs/model-lock.md` 裡那批數字。放進儲存庫是為了讓
**每個人都能在自己機器上重跑**——S12 的延遲門檻在 A 的 M5 與 E 的 5070 Ti 上
看不出問題，只有 B / C / D 那種 4 GB 舊筆電才會撞到。

## 🔴 這些腳本不在專案相依裡，也不該加進去

`requirements.txt` 已經寫明 torch 是硬體相依套件，故意不鎖版本
（macOS ARM 要 CPU/MPS、Windows 要 CUDA wheel）。而且 `pyproject.toml`
與 `uv.lock` 是 W1–W2 就凍結的共管路徑。

**所以請用隔離環境跑，不要動專案相依：**

```bash
uv run --no-project --python 3.11 --with torch --with transformers --with sentencepiece python tools/bench/embed_latency.py
```

`--no-project` 是關鍵：它讓 uv 建立一次性環境，不碰 `.venv`、不改 `uv.lock`。

## 三支腳本

| 腳本 | 量什麼 | 需要什麼 |
|---|---|---|
| `embed_latency.py` | 嵌入模型：查詢編碼延遲、建索引吞吐 | torch + transformers |
| `rerank_latency.py` | 重排序模型：重排 10/20/50 筆的耗時 | torch + transformers |
| `slm_latency.py` | 地端 SLM：TTFT、生成速度、**上下文天花板** | Ollama 已啟動 |

前兩支會下載模型到 HuggingFace 快取（bge-m3 約 2.3 GB、
bge-reranker-v2-m3 約 2.2 GB、text2vec-base-chinese 約 0.4 GB）。
想放別的磁碟就設 `HF_HOME`。

## 條件對齊專案決議，不要自己改

嵌入與重排序**固定 `device="cpu"`、`dtype=float32`**，SLM 的取樣參數
對齊 `packages/shared/models.py` 的 `TEMPERATURE` / `TOP_P` / `SEED`。

改了這些就量不出可比較的數字——那正是 `docs/model-lock.md`
「跨平台決議」那節要避免的事。

## 下載失敗的話

C 在 2026-09-19 實測時，HuggingFace 的 Xet 傳輸後端卡死過兩次
（`.incomplete` 檔 22 分鐘寫進 0 位元組，行程卻沒報錯）。繞法：

```bash
# 退回傳統 HTTP 下載
set HF_HUB_DISABLE_XET=1
```

匿名下載會被限速。`embed_latency.py` 已經把小模型排在前面，
所以就算大的下不來，至少拿得到一組數字。

## 跑完之後

把輸出貼進 `docs/model-lock.md` 對應章節，或開 PR 補上你那台的數字。
**請一併附上 CPU 型號**——決定 S12 過不過得了的是 CPU 單核速度與核心數，
不是顯卡也不是 RAM。
