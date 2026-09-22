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

> 對到 2026-09-22，`main` 在 `f2cdf96`。這張表只寫**已經進 `main`** 的東西。
> 目前沒有開著的 PR —— #38／#39／#41／#42 都已合併。

| 階段 | 步驟 | 狀態 |
|---|---|---|
| 一 訂規矩、做共用的東西 | S1–S5 | 共用層與模型鎖定表已定案；`embed()`／`call_slm()` 已接真模型（bge-m3／Ollama qwen2.5:3b） |
| 二 備語料、做外殼、共做範本模組 | S6–S8 | 語料已抓（194,355 筆，#24）；外殼、路由、範本模組可用 |
| 三 各自寫完自己那一套 | S9–S15 | **進行中**：A、C、E 已可端對端跑完；B、D 尚未建模組 |
| 四 合併 | S16–S21 | A 的入場檢查七項已全過、可掛載；C、E 還差「20 題考題 ≥ 17」這一項 |
| 五 驗收與發表 | S22–S23 | 未開始 |

### 五個模組

實測於 2026-09-22（`make selfcheck MODULE=…`）。

| | 題目 | 進度 | 入場檢查（七項） |
|---|---|---|---|
| A | LINE × 假投資 | 語料 17,764 筆、向量索引、M4 規則層已可跑 | **7/7 可掛載**（考題 19/20） |
| B | **未宣告** | 只有 README | — |
| C | Facebook × 投資詐騙 | 檢索 + 地端模型生成已接上，行動劇本已填 | 5/7 —— 差考題（0/2）與 `REPORT.md` |
| D | 超商店到店物流 × 假冒物流平台客服 | 只有 README | — |
| E | Threads × 網路購物詐騙 | 截圖集、12 階段判定、語料 9,167 筆 | 6/7 —— 差考題（11/20） |

> [!NOTE]
> C 與 E 卡的是同一項，而且 E 的作者刻意停在 11/20：再調參數就是對自編的
> 20 題過擬合。說明書要 17/20 的前提是「考題從 S10 那 60 筆抽」，所以正解是
> 先做 S10、再從真實案例重新出題。詳見下面 `S12` 那一條。

**待全隊決議的事**（都用 `TODO(S編號)` 標在程式碼裡，`grep -rn "TODO(S" .` 找得到）：

- `S1` `.github/team.yml` 的 B 欄題目 —— 五個人只剩這一欄是空的
- `S3` `.github/team.yml` 的 `demo_machine` 還是 `TODO-S3`
- `S6` 手法 × 平台交叉表（語料本身已到位）
- `S12` **路由考題與檢索評估集要不要分成兩份檔案** —— #41 實測混用會得 0/20，
  原因是檢索評估集刻意避開專有名詞，而 `can_handle()` 正是靠那些詞區分類別。
  A 這邊量到同一件事：照規則出的 20 題（含 10 題不含專有名詞），在原本的
  平台判定下只有 10/20。A 改成三態判定後是 19/20，但那只解決了 A 自己
  ——**範本的出題規則還是原樣，C、D、E 會踩到同一個坑**。
  兩份量測記在 `packages/modules/a_tbd/eval/route_questions.yaml` 檔頭

## 怎麼在本機重現 CI

這輪抓到兩次「本機綠、CI 紅」，都是環境差異而不是程式錯。要重現得同時做到
**套件等價**與**檔案等價**（CI 只裝 `dev`，而且 clone 沒有被 `.gitignore`
擋掉的語料與索引）：

```bash
git worktree add /tmp/ciwt HEAD --detach          # 只拿版控裡的東西
uv venv /tmp/civenv --python 3.11
UV_PROJECT_ENVIRONMENT=/tmp/civenv uv sync --extra dev
cd /tmp/ciwt && UV_PROJECT_ENVIRONMENT=/tmp/civenv uv run --no-sync pytest -q
git worktree remove /tmp/ciwt                     # 用完收掉
```

不會動到專案的 `.venv`，也不會動到本機的語料與索引。

> [!WARNING]
> **不要用「攔 import」的方式模擬沒裝套件。** `importlib.util.find_spec()`
> 還是看得到磁碟上的套件，靠它守衛的 `skip` 不會觸發 —— 會量出一個假的
> 結果。這輪兩個人各自用這招驗過，兩次都漏掉真正會紅的測試。

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
