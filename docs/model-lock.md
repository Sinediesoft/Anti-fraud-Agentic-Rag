# 模型版本表（S3）

> 壓縮程度也算版本。同一個模型壓得多跟壓得少表現差很多 ——
> 五個人要用同一份檔案，不是同一個名字。

**狀態：尚未鎖定。** 這張表填完之後，要同步更新 `packages/shared/models.py` 的 `MODEL_LOCK`，
兩邊不一致會讓分數對不起來。

| 用途 | 模型名 | 版本編號 | 壓縮格式 | 鎖定日期 |
|---|---|---|---|---|
| 地端小模型（SLM） | TODO | | | |
| 嵌入模型 | TODO | | | |
| 重排序模型 | TODO | | | |
| 雲端模型 | TODO | | | |

## 候選（說明書 S3）

- **地端小模型**：Qwen2.5-7B ／ Llama-3.2-3B ／ TAIDE-LX-7B
  → 依 S2 盤點的顯卡記憶體選，**以最低配的那台為準**
- **嵌入模型**：bge-m3 ／ multilingual-e5-large ／ text2vec-base-chinese
  → **鎖定後不能換**。五個人各建各的向量庫，但嵌入模型必須相同
- **重排序模型**：bge-reranker-v2-m3
- **雲端模型**：選一家、一個型號、一個版本

## 硬體盤點（S2 第 5 點）

> [!CAUTION]
> 🔴 **混用環境必須多記「作業系統」與「模型後端」兩欄。**
> Apple Silicon 的**統一記憶體**與 NVIDIA 的**顯卡記憶體**不是同一個東西：
> M 系列 16GB 統一記憶體大約可跑 7B 量化模型，與 8GB 顯卡記憶體的 Windows 機相當。
> 兩種數字都要記下來再一起判斷「最低配那台」是誰。

| 成員 | 作業系統 | 模型後端 | 顯示卡 / 晶片 | 顯卡或統一記憶體 | 系統記憶體 |
|---|---|---|---|---|---|
| A | macOS | Metal | | | |
| B | Windows | CUDA / CPU | | | |
| C | Windows | CUDA / CPU | | | |
| D | Windows | CUDA / CPU | | | |
| E | Windows | CUDA / CPU | | | |

## 🔴 跨平台決議（S3 鎖定時一併寫死）

> [!CAUTION]
> **嵌入模型與重排序模型固定 `device="cpu"`、`dtype=float32`。**
>
> 同一個模型在 Mac 的 MPS（預設 fp16）與 Windows 的 CUDA/CPU（fp32）上算出來的向量
> 有低位數值差異，分數接近時可能讓 **Recall@5 差一兩個百分點** ——
> 那就違反了「五個人的分數要能互相比較」這個前提。
>
> 成本很低：嵌入只在建索引時跑一次（幾千筆，CPU 幾分鐘）與每次查詢一句話（毫秒級）。

**地端 SLM 相反 —— 走各自 OS 的原生 Ollama，用自己的 GPU：**

| 系統 | Ollama 安裝 | 後端 |
|---|---|---|
| macOS | `brew install ollama` 或官網安裝檔 | Metal |
| Windows | 官網安裝檔（原生支援，不需要 WSL） | CUDA 或 CPU |

兩邊 `ollama pull` 同一份模型、對同一個編號，程式統一透過
`OLLAMA_HOST=http://127.0.0.1:11434` 連過去。
🔴 **不要把地端模型放進 Docker** —— Docker Desktop for Mac 傳不了 Metal，容器內只有 CPU。

詳見 [說明書補充：跨平台差異](說明書補充-跨平台差異.md)。

## 金鑰

- 雲端金鑰用環境變數（`.env`），**不要進儲存庫**
- 第一週就設用量上限
- 保管人：TODO
