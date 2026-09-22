# 防詐 Copilot

有人被詐騙時，通常只會用自己的話講「群組裡的老師叫我先入金才能出金」，
而官方的 165 網站是逐字比對，這句話查下去是 0 筆。

這個產品讓他用自己的話講、或直接上傳截圖，系統就能告訴他
**你遇到的是哪一類詐騙、你走到哪一步了、接下來該做什麼**。

對應《開發步驟示範說明書 v2.1》23 個步驟、七週、五人完全獨立開發。

---

## 快速開始

```bash
make install                  # 建環境、裝套件、掛上 pre-commit
make test                     # 跑全部測試
make check                    # 越界檢查
make run MODULE=a_tbd         # 啟動介面（單一模組）
make run MODULE=all           # 啟動介面（全部模組）
make selfcheck MODULE=a_tbd   # S16 入場檢查七項
```

> [!CAUTION]
> ### 🔴 Windows 開發者必讀
>
> 本隊是 **1 台 macOS ＋ 4 台 Windows**。說明書假設全隊環境一致，有幾個步驟照原文做會卡住。
> **動手前請先讀 [說明書補充：跨平台差異](docs/說明書補充-跨平台差異.md)**，重點三條：
>
> 1. 🔴 **Windows 沒有 `make`** —— 直接打原始指令即可，不需要安裝（對照表見下）
> 2. 🔴 **Windows 的 Python 預設編碼是 cp950**，中文語料會爆 —— 請執行 `setx PYTHONUTF8 1`
> 3. 🔴 **Git for Windows 安裝時選 `Checkout as-is`** —— 否則 `Makefile` 會變 CRLF 而失效
>    （本 repo 已用 `.gitattributes` 兜底，但自己設一次比較保險）

### Windows 指令對照表

| 說明書寫的 | 🔴 Windows 改打這個 |
|---|---|
| `make install` | `uv sync --extra dev --extra ui` |
| `make test` | `uv run pytest -q` |
| `make check` | `uv run python tools/check_boundaries.py` |
| `make run MODULE=a_tbd` | `uv run streamlit run app/ui.py -- --module=a_tbd` |
| `make eval MODULE=a_tbd` | `uv run python -m app.cli eval --module=a_tbd` |
| `make selfcheck MODULE=a_tbd` | `uv run python -m app.cli selfcheck --module=a_tbd` |

完整對照與其他差異見 [說明書補充：跨平台差異](docs/說明書補充-跨平台差異.md)。

## 目前狀態

> 對到 2026-09-22。這張表只寫**已進 `main` 或已在分支上跑得出來**的東西，
> 開著的 PR 另列在下面 —— 兩者混寫會讓人以為進度比實際快。

| 階段 | 步驟 | 狀態 |
|---|---|---|
| 一 訂規矩、做共用的東西 | S1–S5 | 共用層與模型鎖定表已定案；`embed()`／`call_slm()` 已接真模型（bge-m3／Ollama qwen2.5:3b） |
| 二 備語料、做外殼、共做範本模組 | S6–S8 | 語料已抓（194,355 筆，PR #24）；外殼、路由、範本模組可用 |
| 三 各自寫完自己那一套 | S9–S15 | **進行中**：A、C、E 已可端對端跑完；B、D 尚未建模組 |
| 四 合併 | S16–S21 | A 的入場檢查七項已全過；C、E 還差「20 題考題 ≥ 17」這一項 |
| 五 驗收與發表 | S22–S23 | 未開始 |

### 五個模組

| | 題目 | 進度 | 入場檢查（七項） |
|---|---|---|---|
| A | LINE × 假投資 | 語料 17,764 筆、向量索引、M4 規則層已可跑 | **7/7 可掛載**（考題 19/20） |
| B | 未宣告 | 只有 README | — |
| C | Facebook × 投資詐騙 | 檢索 + 地端模型生成已接上，行動劇本已填 | 5/7 —— 差考題與 `REPORT.md` |
| D | 超商物流 × 假冒客服（PR #39 宣告中） | 只有 README | — |
| E | 求職平台 × 人頭帳戶（PR #41 要改為 Threads × 網路購物詐騙） | 截圖集與階段判定已做 | 6/7 —— 差考題 |

### 開著的 PR

- **#41** 模組 E 改題目為 Threads × 網路購物詐騙，接上 12 階段判定
- **#39** 模組 D 宣告題目為超商物流 × 假冒客服
- **#38** S3 把 `embed()`／`call_slm()` 接上真模型 —— 這份工作已含在 `feat/整合-a-c` 裡，兩邊重複

> [!IMPORTANT]
> `feat/整合-a-c` 領先 `main` 23 個 commit（S3 模型接線 + 模組 C + 模組 A），還沒開 PR。
> 在它合併之前，`main` 上跑不出真模型的結果。

**待全隊決議的事**（都用 `TODO(S編號)` 標在程式碼裡，`grep -rn "TODO(S" .` 找得到）：

- `S1` `.github/team.yml` 的 B 欄題目（A／C 已填，D／E 待 PR #39／#41 合併）
- `S3` `.github/team.yml` 的 `demo_machine` 還是 `TODO-S3`
- `S6` 手法 × 平台交叉表（語料本身已到位）
- `S12` **路由考題與檢索評估集要不要分成兩份檔案** —— PR #41 實測混用會得 0/20，
  原因是檢索評估集刻意避開專有名詞，而 `can_handle()` 正是靠那些詞區分類別。
  A 這邊量到同一件事：照規則出的 20 題（含 10 題不含專有名詞），在原本的
  平台判定下只有 10/20。A 改成三態判定後是 19/20，但那只解決了 A 自己
  ——**範本的出題規則還是原樣，C、D、E 會踩到同一個坑**。
  兩份量測記在 `packages/modules/a_tbd/eval/route_questions.yaml` 檔頭

## 資料夾

```
app/                     外殼：載入模組、路由、解鎖、輸出檢核、介面
packages/contracts/      介面規格（四個進入點、Verdict、pack.yaml schema）
packages/shared/         共用三樣：deid 去識別化、models 模型呼叫、eval 分數計算
packages/modules/
  _template/             範本模組（底線開頭，外殼掃描時自動略過）
  a_tbd/                 模組 A ← 你的，平台 × 手法待定
  b_tbd/ … e_tbd/        其他四人的佔位
tools/                   機器人：越界檢查、修改範圍檢查
tests/                   規矩全部寫成測試
docs/                    架構、模型版本表、GitHub 速查、怎麼新增第六個模組
data/                    語料放這裡（不進版控）
```

## 三條從頭貫穿到尾的規矩

1. **去識別化、模型呼叫、分數計算三樣不准自己寫** —— 一律用 `packages/shared/`。
   `tools/check_boundaries.py` 是唯一的執法者，pre-commit 與 CI 都會跑
2. **五個人的模組互不相認** —— 你的模組不准 import 別人的模組
3. **共用的東西第二週結束就凍結** —— 之後只修 bug、不加功能

## 文件

- 🔴 **[說明書補充：跨平台差異](docs/說明書補充-跨平台差異.md)** ← Windows 組員動手前必讀
- [架構](docs/architecture.md)
- [GitHub 協作速查](docs/github-協作速查.md) ← 每天都會用
- [模型版本表](docs/model-lock.md)
- [怎麼新增第六個模組](docs/新增模組.md)
- 開發步驟示範說明書.pdf —— 不進版控（repo 公開），請到共用雲端硬碟取得
