# f：合約違法比對（提案，尚未認領）

> 這不是 A–E 那種「平台 × 詐騙手法」模組，是一個獨立小工具的架構草案。
> 討論脈絡見開這個資料夾的那次對話；這份 README 只留結論。

## 這是什麼

丟一份合約文字進來，逐條比對是否疑似牴觸法規或公告的定型化契約規則，
吐出一份「第幾條／命中哪條法規／為什麼疑似違法／建議行動」的報告。
不判斷詐騙類型，不出 `Verdict`——這是法律合規檢查，不是受害者判讀。

## 為什麼沒有 `pack.yaml`

刻意不放。`app/registry.py` 只掃有 `pack.yaml` 的資料夾，沒有它，外殼永遠不會
載入這個模組，不會跟 A–E 搶 `can_handle()`。輸出走獨立 CLI，不進 Streamlit。

放在 `packages/modules/` 底下（而不是 `tools/`）是因為 `tools/check_boundaries.py`
對整個 `packages/modules/` 掃描，跟有沒有 `pack.yaml` 無關——放這裡可以白拿
「不准 import 模型/連網套件、不准 import 別人的模組、不准自己重寫 deid/eval」
這層防呆。

## 管線（沿用 m1~m5 命名習慣，但不掛 `playbook.yaml`）

| 檔案 | 做什麼 |
|---|---|
| `m1_ingest.py` | 讀入合約文字，呼叫 `shared.deid.mask()` 拿到 `MaskedText`（跟其他模組同一條硬規則：原文不進雲端） |
| `m2_segment.py` | 條款切分。規則式拆條號/項號，不碰模型，保留原文 offset |
| `m3_retrieval.py` | 對每條條款做 RAG 檢索，抓最相關的法規／應記載不得記載事項 |
| `m4_judgement.py` | 呼叫 `shared.models` 判斷「這條是否疑似牴觸」與原因。保守——不確定就標「建議諮詢律師」，不武斷定罪 |
| `m5_report.py` | 組成逐條報告，借用 `contracts.LegalRef`／`contracts.ActionItem`／`contracts.DISCLAIMER` 當零件 |
| `cli.py` | 進入點：`python -m packages.modules.f_合約_違法比對.cli <合約檔案>` |
| `schemas.py` | 這個工具自己的報告 model（不進 `packages/contracts/`，那層是 W1 凍結的 A–E 共用介面，合約審查不屬於那個語境） |

## 資料來源（v1 只做前兩塊）

1. **母法條文摘錄**：全國法規資料庫（[Open API](https://law.moj.gov.tw/api/swagger/index.html)／[整批下載](https://law.moj.gov.tw/Service/LawData.aspx)）。
   只挑二三十部跟合約違法高相關的（民法、消保法、多層次傳銷管理法、個資法、
   證券投資信託及顧問法、公平交易法、就業服務法…），格式比照 `c_tbd/pack.yaml`
   的 `knowledge_refs`：title／article／version_date。
2. **定型化契約應記載及不得記載事項**：[行政院消費者保護會](https://cpc.ey.gov.tw/Page/31967FA5ED534142)。
   這批是現成的「哪些條款違法」清單（寫了「不得記載」的條款＝無效條款），
   比啃母法全文有效率，是這個工具的 ground truth。優先做跟 A–E 現有手法
   重疊度最高的幾類：投資顧問、多層次傳銷、求職媒合。
3. **裁判書**（v2，非必要）：[司法院裁判書開放 API](https://opendata.judicial.gov.tw/)，
   量級是百萬筆，只能用關鍵字篩子集當補充佐證，不整包拉。

語料放這個資料夾自己的 `data/`。消保法這類通用法規未來可能有第二個模組
也用得到，真的發生時再談要不要上升成 `packages/shared/` 的第四樣共用工具——
現在「三樣不准自己寫」沒把法規語料算進去，先不動共用層。

## 還沒決定的事

- 要不要真的認領、算誰的工——`.github/CODEOWNERS` 與 `.github/team.yml`
  沒有動，等團隊在 issue 裡回覆
- OCR 掃描件輸入留到 v2，v1 只吃貼上的純文字
