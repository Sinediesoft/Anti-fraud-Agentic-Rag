# 模組 C — Facebook × 投資詐騙

**狀態：移植進行中。** M3 檢索核心已落地並有測試；M2／M4／M5 仍是樣板。

這個模組不是從 `_template` 從零長出來，而是從另一個本機 repo（防詐騙 chat-bot）
移植。那是一套已經跑得動的地端 RAG 防詐查詢機器人：6 份案例卡語料、llama.cpp
生成、bge-m3 向量、FastAPI + React 前後端。

**為什麼移植得動**：它的 `contracts/` 與本專案同源 —— 一樣的四個進入點
（`can_handle` / `analyze` / `info` / `health`）、一樣用 Protocol 而非繼承、
一樣「一個資料夾一個模組、彼此不准互相 import」。所以要做的是對映，不是重寫。

**為什麼不能整包搬**：那是一個完整應用，有自己的外殼、共用層與契約；本專案
那三層已經存在而且是全員共管的凍結層。能進來的只有 domain 層。

---

## 選題：Facebook × 投資詐騙

來源那套的 `investment.toml`（飆股群組／老師帶單）正好是這條路徑的下半段：
**FB 廣告或社團接觸 → 導流到 LINE 群組 → 老師帶單 → 假平台出不了金。**

> [!IMPORTANT]
> `job.toml`（高薪打工淪為車手）**不移植** —— E 已佔走「求職平台 × 人頭帳戶」。
> 那些詞已經進了 `pack.yaml` 的 `negative_terms`，實測會讓求職類案子拿 0.00 分。
>
> ⚠️ `team.yml` 的 `platform` / `tactic` 目前**五個人全是 TODO**，S6 還沒正式
> 決議。E 先自己填進 `pack.yaml`，本模組跟進 —— 兩份不同步要儘快處理。

---

## 一、已經搬進來的

| 來源 | 落點 | 改了什麼 |
|---|---|---|
| `shared/facets.py` | `facets.py` | 🔴 `derive()` 從**驗證**改成**推斷**，見下 |
| `shared/store.py` | `m3_retrieval.py::NumpyStore` | `Doc` → `Case`；保留增量索引與模型相容性檢查 |
| `shared/retriever.py` | `m3_retrieval.py::Retriever` | `embed()` 改走 `shared.models`（規矩一） |
| `tools/build_corpus.py::coverage()` | `m1_corpus.py::coverage()` | 幾乎照搬，見下 |
| — | `m1_corpus.py::build_subset()` | 新寫的，不是移植 |
| — | `tests/test_module_c.py` | 32 個測試 |

### `ingest.py` 其實搬不動

移植計畫原本列 `shared/ingest.py` → `m1_corpus.py`。實際動手後發現它**整支
在解析 TOML 案例卡**，而本專案讀的是 165 的 parquet —— 格式不同，不是改
輸入路徑就能用。所以 `build_subset()` 是新寫的。

真正搬得動的是 `tools/build_corpus.py` 的 `coverage()`，而那是那支腳本
最有價值的一段：**詞彙表裡有、語料裡沒有的值，使用者問得出過濾條件然後
篩到 0 筆 —— 那比抽不出條件更糟，因為它會保證回答「沒有」。**

### 切語料：實測結果（2026-09-20，194,355 筆）

```
掃了       194,355 筆
手法命中    30,811 筆   <- pack.yaml 的 stats.total_cases
平台交集    10,051 筆   <- stats.platform_cases（32.6%）
```

165 的六個欄位裡**沒有平台欄**，平台只能從內文推斷。所以同時回報兩個數字：
只給後者會讓人以為「我的語料就這麼多」，實際上是「我認得出來的就這麼多」。
比例低於 10% 會印警告。`--tactic-only` 是語料太少時的退路，但那等於放棄
「平台 × 手法」這一維，要寫進報告。

### 🔴 165 的 `label` 欄不是受控分類

實測 194,355 筆裡有 **1,003 種標籤值**，混了三種東西：

| 種類 | 例 |
|---|---|
| 官方分類 | `假投資詐騙` 27,718、`假投資` 2,509 |
| 空白／換行變體 | `'假投資詐騙 '`、`'\n\n假投資詐騙'`、`' \n \t\n假投資詐騙'` |
| 受害者自己寫的標題 | 「投資夢，成詐騙」「慈惠APP的謊言：⋯百萬騙局」 |

所以比對用**子字串**而不是等值：前兩種吃得下，第三種放棄（自由文字沒有規則抓得完）。

**邊界決定**：`假交友(投資詐財)詐騙` 有 13,945 筆，是投資詐財但走交友路徑，
屬於別人的組合。子字串會經由「投資詐財」咬到它，所以靠 `negative_terms` 的
「交友」擋掉 —— 沒有這條的話手法命中會是 46,554 而不是 30,811。

> [!NOTE]
> 標籤排除沿用 `negative_terms` 而不是新開 `label_excludes` 欄位：
> `PackSpec` 設了 `extra="forbid"`，而它在凍結的 `packages/contracts/` 裡。
> 語意本來就對得上 ——「出現這些字多半是別人的類型」。

### 🔴 移植時最大的轉折：`derive()` 的職責翻過來了

來源的語料是**自己訂格式、自己寫**的 6 張 TOML 案例卡，欄位本來就乾淨，
所以 `derive()` 的工作是**驗證** —— 填錯的值當場報錯。

本專案的語料是 165 的原始案件記錄，只有六個欄位（編號、日期、縣市、縣市代號、
內文、標籤），**沒有管道、付款方式、對象這些欄**。所以只能退回去從自由文字
**推斷**，也就是中介格式原本要避免的那個模式。

這不是退步，是語料性質決定的。代價（漏抓與錯抓兩種相反的錯誤）寫在
`facets.derive_from_text()` 的註解裡，**目前兩者都沒有量過**。

### 三層退路（S13 第 4 點）

```
1. 向量檢索     embed() 走 shared.models      ← S3 鎖定嵌入模型後才接上
2. 結構化過濾   facets.py，純規則、零模型      ← 永遠可用
3. 關鍵字重疊   _overlap_score                ← 現在實際跑的是這層
```

第 1 層的程式碼是**完整的**，鎖定後把 `m3_retrieval.MODEL_READY` 打開就接上。

---

## 二、還沒搬的

| 來源 | 落點 | 卡在哪 |
|---|---|---|
| `shared/knowledge.py::SYSTEM` | `m4_judgement.py` | 提示詞要配合 S13 的 12 則示範題重寫 |
| `shared/chat_bot.py` | `m5_agent.py`（拆一半，生成那半刪掉） | 最大的一支，要等 M4 定案 |
| `module.py::_actions()` | `playbook.yaml` | 從程式碼變成資料，補 `urgency` 欄位 |
| `tools/probe_threshold.py` | `tools/bench/` | 門檻量測，**語料換了必須重量** |

## 三、被共用層取代，不搬

`contracts/`、`shared/registry.py`、`shared/llm.py`、`shared/settings.py`、
`providers.py` —— 本專案的 `packages/contracts/`、`app/registry.py`、
`packages/shared/models.py` 已有等價物，而且是凍結的共管層。

## 四、沒有位置，留在原 repo

`api/`（FastAPI，且 `httpx` 在禁止清單）、`frontend/`（React）、
`shared/api_client.py`、`onperm/llm.py`（直接呼叫 `llama_cpp`，`MODEL_PACKAGES` 明文禁止）。

## 五、必須從零寫的

| 要的東西 | 現況 |
|---|---|
| `m2_vision.py` | 來源完全沒有截圖理解。那是 S11 一整個步驟 |
| `evaluate.py` | 來源沒有分數計算。本專案一律呼叫 `shared.eval` |
| **去識別化** | 來源的策略是「訊息不離開這台電腦」；本專案是「一律用 `shared.deid`」。兩者不衝突，但來源那條流水線確實沒有這一步。`m3_retrieval.search()` 已補上，M5 還要再補 |

---

## 六、已知待辦

1. **`DEFAULT_MIN_SCORE` 現在是佔位符 0.0，不是量出來的。** 來源對 6 張案例卡
   量出的門檻在這裡沒有意義 —— 語料換成十幾萬筆原始記錄，尺度完全不同。
   S12 要重量。
2. **「放掉 min_score」有副作用。** 有過濾條件時會繞過分數門檻，而這個語料的
   詞彙表詞很常見（「投資」「廣告」「轉帳」），離題問句容易誤觸。
3. **`numpy` 與 `pyarrow` 是未宣告的相依。** 目前靠 `streamlit` 的傳遞相依才
   裝得到，不在 `requirements.txt` 裡。S3 的 `ml` extra 補上之前，沒裝
   `--extra ui` 的人會 import 失敗。`pyarrow` 走延遲 import（跟
   `tools/fetch_corpus_165.py` 同一個做法），所以至少 import 模組不會炸。
4. **推斷的準確率還沒量過。** `facets.derive_from_text()` 的漏抓與錯抓
   兩種錯誤都只有註解描述，沒有數字。要量得等 S12 的標註評測集。

   > 早前這裡曾寫「`target` 這一維在 165 語料上必然是空的」—— **那是錯的**。
   > 當時用 3 筆合成資料得出結論，換成 10,051 筆真實語料後，四個維度
   > 全部 10/10 用滿。合成資料量不出覆蓋率，這件事本身值得記著。

## 七、這個資料夾只有 C 能改

見 `.github/CODEOWNERS`，別人動到會被 `tools/check_ownership.py` 擋下來。
反過來也一樣 —— 需要別人改東西請開 Issue。
