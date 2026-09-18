# GitHub 協作速查（貼在螢幕旁邊）

五個人、五台電腦、一個產品。這一章每天都會用到。

> [!CAUTION]
> 🔴 **Windows 組員第一次設定時多做一步**（說明書 §1.3 沒寫到）：
> ```
> git config --global core.autocrlf input
> ```
> 否則 checkout 時 `Makefile` 會被轉成 CRLF 而失效。
> 本 repo 的 `.gitattributes` 已經兜底，但自己設一次比較保險。
> 其餘差異見 [說明書補充：跨平台差異](說明書補充-跨平台差異.md)。

## 每天的固定流程

```bash
# 早上 — 開工前先同步
git switch main
git pull
git switch -c feat/mod-a-截圖標註        # 分支名：feat/mod-<代號>-<在做什麼>

# 工作中 — 完成一件有意義的小事就存一次
git status
git add packages/modules/a_tbd/pack.yaml
git commit -m "feat(mod-a): 填入平台關鍵詞"

# 下班前 — 沒做完也要推
git push -u origin feat/mod-a-截圖標註   # 第一次
git push                                  # 之後

# 做完一段 — 開 PR
gh pr create --title "feat(mod-a): …" --body "完成 S9。案例數 2,430 筆，已過門檻。"
gh pr status

# 合併後 — 回到起點
git switch main && git pull
git branch -d feat/mod-a-截圖標註
```

## commit 訊息格式

`類型(範圍): 做了什麼`，類型只有四種：`feat` `fix` `docs` `chore`

- 好：`feat(mod-a): 新增未到貨階段的行動清單`
- 壞：`update` `修改` `aaa` `最終版` ← 兩週後自己也看不懂

## PR 要寫什麼（三行）

1. 完成了哪一步（S 編號）
2. 關鍵數字（實測值 + 門檻）
3. 要提醒審的人的事

合併時選 **Squash and merge**，合併完把分支刪掉。

## 審查只看三件事

因為五套程式互不相認，審自己模組的 PR 不用細看程式邏輯（那是作者的責任）：

1. 有沒有動到 `packages/shared/`、`app/`、`packages/contracts/` 或別人的資料夾
2. 有沒有偷偷自己實作去識別化或模型呼叫
3. CI 有沒有綠燈

三件都過就可以合併。

## 出狀況了怎麼救

| 狀況 | 怎麼辦 |
|---|---|
| 不小心直接在 main 上改了（還沒 commit） | `git stash` → `git switch -c feat/…` → `git stash pop` |
| push 被拒絕，說遠端有你沒有的東西 | `git pull` 處理完再 push |
| 想撤回上一個 commit（還沒 push） | `git reset --soft HEAD~1` |
| 改壞了想回到上次 commit 的狀態 | `git restore 檔名` |
| 分支落後 main 太多 | `git switch 你的分支` → `git pull origin main` → 解衝突 |
| 完全搞不清楚狀態 | `git status` ＋ `git log --oneline -10` |
| 真的弄不好了 | 整個資料夾改名備份，重新 clone，把改過的檔案複製回去 |

## 衝突處理

```bash
git switch feat/mod-a-你的分支
git pull origin main
# 打開衝突的檔案，刪掉 <<<<<<< ======= >>>>>>> 記號，通常兩邊都要留
git add 檔名
git commit -m "chore: 解決衝突"
git push
```

**三個原則**：不要用「我的全部蓋過去」；看不懂就找對方一起看；最好的處理是不要撞到。

## 容易撞的五個地方

| 地方 | 怎麼避免 |
|---|---|
| `packages/contracts/` | W1 末凍結，要動先在群組講且全員同意 |
| `packages/shared/` | W2 末凍結，之後只修 bug，改完全員重跑分數 |
| `app/` | W2 末凍結，W6 合併週才會再動 |
| `Makefile` | 加指令前先 pull，只加不改別人的 |
| `uv.lock` | 要裝新套件先在群組講一聲 |

## 每週節奏

| 時間 | 做什麼 |
|---|---|
| 每天早上 | pull、開新分支、掃一眼看板 |
| 每天下班前 | push（沒做完也要推） |
| 週一 | 15 分鐘站立會：做完什麼、要做什麼、卡在哪 |
| 每週 | 各自跑 `make eval`，把數字貼在共用文件 |
| 週五 | 五項同步檢查：分支都合了、main 跑得動、沒有超過三天的分支、沒有掛著的 PR、跨界 Issue 都有人接 |

## 最重要的三句話

1. **每天早上 pull、每天下班 push。** 做到這件事，八成的合併災難不會發生。
2. **一條分支只做一件事，不要活超過三天。** 分支越老越難合。
3. **不確定就先 `git status`，真的弄不好就重新 clone。**
