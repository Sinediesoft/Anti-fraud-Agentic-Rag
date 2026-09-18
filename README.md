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

| 階段 | 步驟 | 狀態 |
|---|---|---|
| 一 訂規矩、做共用的東西 | S1–S5 | 骨架已備，內容待全隊決議 |
| 二 備語料、做外殼、共做範本模組 | S6–S8 | 外殼與範本骨架已備，語料待抓 |
| 三 各自寫完自己那一套 | S9–S15 | 未開始 |
| 四 合併 | S16–S21 | 未開始 |
| 五 驗收與發表 | S22–S23 | 未開始 |

**待全隊決議的事**（都用 `TODO(S編號)` 標在程式碼裡，`grep -rn "TODO(S" .` 找得到）：

- `S1` `.github/team.yml`、`.github/CODEOWNERS` 的 GitHub 帳號
- `S3` `docs/model-lock.md` 與 `shared/models.py` 的模型版本表
- `S6` 全量語料與手法 × 平台交叉表
- **模組 A 的「平台 × 詐騙手法」尚未決定** → `packages/modules/a_tbd/pack.yaml`

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
