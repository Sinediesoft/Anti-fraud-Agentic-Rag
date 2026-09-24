# 模組 E — Threads × 網路購物詐騙

> 負責人：c85016921920-design　｜　題目定案：2026-09-21（PR #41）　｜　對到 2026-09-24
>
> 🔵 **工作在 `feat/mod-e-threads` 分支，不在 `main`。**
> PR #41 合併之後的工作刻意不再合併回 main，所以在 main 上看不到進度。
>
> ```bash
> git fetch --all && git checkout feat/mod-e-threads
> ```

---

## 一句話

**你在 Threads 上看到便宜貨、私訊賣家、對方叫你加 LINE，然後跳出「未完成實名制
認證」——把這段經過講出來，它告訴你這是不是詐騙、你的錢和帳戶目前到哪一步了、
接下來該打哪支電話。每一條判斷都附上真實案例編號與日期縣市。**

---

## 我負責哪一格

| 項目 | 內容 |
|---|---|
| 平台 | **Threads**（起點在 Threads，導流到 LINE 之後的環節也算） |
| 手法 | 網路購物詐騙 |
| 語料 | **4,140 筆**（門檻 2,000） |
| 方案 | 付費解鎖 |

**平台用「起點」定義，不是「只出現這個平台」。** Threads 命中 12,743 筆，
其中 57.2% 會導流到 LINE，但兩者都出現的 7,288 筆裡 **99.5% 是 Threads 先出現**
（只有 34 筆反過來）。所以這些案例的起點都是 Threads，導流到 LINE 是後續環節。
做 LINE 的組員處理起點在 LINE 的案例（例如群組假投資），兩邊不衝突。

## 與模組 D 的界線（2026-09-23 談定）

D 的題目是「超商店到店物流 × 假冒物流平台客服」，與本模組重疊：
E 的語料 33.1% 命中 D 的完整核心鏈，D 的核心鏈 37.0% 起點在 Threads。

**照「平台軸」切，不照「話術」切：**

| | 歸屬 | 理由 |
|---|---|---|
| 超商物流（賣貨便／交貨便／全家／7-11／店到店） | **D** | 那是 D 自己宣告的平台軸 |
| 假客服、實名認證話術 | **E** | 那是話術不是平台，在整個購物詐騙族佔 70%，是共同環節 |

D 一度提議連假客服、實名認證一起歸他。實測那樣切，E 只剩 1,867 筆（不到門檻），
而且 12 階段流程的第 4、6、7 階全歸零，`decisive` 訊號消失，模組失去攔截點。

實作在兩處，**必須一致**：`m1_corpus.EXCLUDE_PATTERN`（切語料時排除，讓出
5,038 筆）與 `module.can_handle`（命中時壓到 `route_hint_min` 之下）。
語料排除什麼，路由就要排除什麼 —— 不一致的話，模組會認領自己語料裡沒有的案子。

> ⚠ 這個界線目前是 E 單方面實作的，**還沒跟 D 確認過**。
> 覺得切錯了請直接找我，不要等到驗收才講。

## 進度

| 步驟 | 狀態 |
|---|---|
| S9 語料切分 | ✓ 194,355 → Threads 12,743 → 網購 9,187 → 讓出超商物流 → **4,140** |
| S10 標準答案 | ✓ 60 筆（v4，標了四輪）　**kappa 待第二位標註者** |
| S11 模擬截圖 | ✓ 20 張＋標註，全部程式產生、可重現（`screenshots/_sources/`） |
| S12 檢索 | ✓ BM25 ＋ 語意向量（bge-m3）RRF 融合 |
| S16 入場檢查 | ✓ **七項全過** |

### 分數（`uv run python -m app.cli eval --module=e_tbd`）

| 指標 | 門檻 | 現在 |
|---|---|---|
| can_handle 命中 | ≥ 0.85 | **0.850**（壓線） |
| Recall@5 | ≥ 0.75 | 0.950 |
| 延遲 p95 | ≤ 1.0s | ~0.000 |
| **標註一致性 kappa** | **≥ 0.70** | **⚠ 缺 rater2** |

完整的自評與已知限制見 [`REPORT.md`](REPORT.md)。

## 🙏 徵一位標註者（10–15 分鐘）

**這是唯一還沒過的門檻，而且一個人做不到** —— kappa 是評分者**間**信度，
需要兩個人各標一次同一批 60 筆。

- 標註頁：跑 `uv run python packages/modules/e_tbd/make_gold_sample.py`
  產生 `data/gold_標註.html`，用瀏覽器開，鍵盤選五個階段之一
- 頁面**不帶任何參考答案**（第三輪那次帶了上一輪答案，kappa 0.906 卻分不出
  是「判準修好」還是「照抄」—— 拿掉參考重判 14 筆，13 筆改變）
- ⚠ **含真實案例原文，請走實體或團隊共用硬碟交付，不要用通訊軟體**

願意幫忙的請找我，我把檔案給你。

## 怎麼跑起來

```bash
git checkout feat/mod-e-threads
uv sync --extra dev --extra ui --extra data

# 🔴 這兩個產出不進版控，git pull 拿不到，一定要自己跑一次
uv run python packages/modules/e_tbd/m1_corpus.py     # 語料子集，應為 4,140 筆
uv sync --extra ml
uv run python -m app.cli index --module=e_tbd         # 向量庫，約 1 小時

uv run python -m app.cli selfcheck --module=e_tbd     # 七項全過
uv run python -m app.cli eval --module=e_tbd
```

> ⚠ 向量庫筆數與語料對不上時，`_vector_scores()` 會**靜默退回純 BM25** ——
> 不報錯、不提示，只是少了一半能力。語料重切過之後一定要重建。

## 檔案

```
m1_corpus.py              語料切分（含與 D 的界線 EXCLUDE_PATTERN）
m3_retrieval.py           BM25 ＋ 向量 RRF 融合檢索
m4_judgement.py           判讀：接上 threads_stages
threads_stages.py         12 階段流程與 Harm 五級（純標準庫）
module.py                 ThreadsShoppingModule：can_handle / analyze
pack.yaml                 路由詞表與門檻
playbook.yaml             五個階段各自的行動建議
evaluate.py               分數（一律呼叫 shared.eval，不自己寫指標）
eval/                     考題、標準答案、相關性判定（不含案例原文）
screenshots/              20 張模擬截圖＋標註
  _sources/               產生它們的 shots.yaml 與 render.py（可重現）
data/                     語料子集、標註頁（含原文，不進版控）
REPORT.md                 S15 自評報告
```

## 兩個關鍵設計

**一、「是不是詐騙」與「損失多少」是兩個獨立維度，不壓成單一風險分數。**
壓成一個會出現「已經把錢匯給陌生人，卻顯示低風險」的矛盾。所以判定回傳
`Verdict`＋`Confidence`（是不是詐騙、多有把握）與 `Harm` 五級
（尚未付款／已付款／已重複付款／已交付帳戶控制權／帳戶遭盜用）兩組結果。

**二、沒有「安全」這個結論。** 語料 194,355 筆全部是詐騙報案，一筆正常交易都沒有，
系統沒有資格說任何情況安全。訊號不足時走 fallback 要求補充細節，
最低風險等級是 medium 不是 low。
