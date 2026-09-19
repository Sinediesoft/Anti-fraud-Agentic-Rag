"""PR 機器人：把 CI 的結果翻成白話，附上可以直接複製的修復指令。

這支不判斷對錯，只負責轉譯 —— 對錯是前面那幾個檢查的事。它解決的是
「紅燈了，但我不知道要幹嘛」：組員裡有四個人第一次用 GitHub，叫他們
自己點進 Actions 翻 log 太苛刻。

它也順便回答 ci.yml 裡那個問題：ubuntu 綠、windows 紅 —— 這是相容性
問題還是程式真的錯了？兩者的處理方式完全不同，值得直接寫出來。

留言是「黏性」的：用隱藏標記找到自己上次那則，就地更新，不會每推一次
commit 就多一則。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

API = "https://api.github.com"
MARKER = "<!-- claude-pr-report -->"

# 環境對齊追蹤表。機器人會讀它，看這個 PR 的作者還有哪幾項沒回報。
REPO_ROOT = Path(__file__).resolve().parent.parent
ALIGN_DOC = REPO_ROOT / "docs" / "環境對齊追蹤表.md"
PENDING_MARK = "⬜"  # 未回報
MISSING_MARK = "❌"  # 回報了，但缺這項


def api(path: str, token: str, method: str = "GET", body: dict | None = None):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def job_results(repo: str, token: str, run_id: str) -> dict[str, str]:
    """{job 名稱: 結論}。還在跑的（例如這支自己）結論是 None，一律當 running。"""
    jobs = api(f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", token) or {}
    return {j["name"]: (j.get("conclusion") or "running") for j in jobs.get("jobs", [])}


def pick(results: dict[str, str], *needles: str) -> str | None:
    """找名稱同時含有這些字串的 job。名稱有全形括號，不要寫死。"""
    for name, conclusion in results.items():
        if all(n in name for n in needles):
            return conclusion
    return None


def _md_tables(md: str) -> list[tuple[list[str], list[list[str]]]]:
    """把 markdown 切成 (表頭, 資料列)。只認連續的 | 開頭行。"""

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    out, block = [], []
    for line in md.splitlines() + [""]:
        if line.lstrip().startswith("|"):
            block.append(line)
            continue
        if len(block) >= 3:  # 表頭 + 分隔線 + 至少一列
            out.append((cells(block[0]), [cells(x) for x in block[2:]]))
        block = []
    return out


def pending_items(author: str) -> tuple[str | None, list[str], list[str]]:
    """這個人在對齊表裡的待處理項目。回傳 (代號, 未回報, 要補裝)。

    ⬜ 與 ❌ 是兩件事：前者是「不知道」，後者是「知道而且缺」。兩種都要
    提醒，但講法不一樣 —— 一個是去跑指令，一個是去安裝。

    代號從文件自己的表頭反查 —— 第一張表的欄位長這樣：A<br>`Sinediesoft`。
    這樣就不必再去讀 team.yml（那要 yaml 套件，而這支刻意只用標準函式庫）。
    """
    if not ALIGN_DOC.exists():
        return None, [], []
    tables = _md_tables(ALIGN_DOC.read_text(encoding="utf-8"))

    needle = f"`{author}`".lower()
    code = None
    for header, _ in tables:
        for cell in header:
            if needle in cell.lower():
                m = re.match(r"^([A-E])\b", cell.strip())
                if m:
                    code = m.group(1)
                    break
        if code:
            break
    if code is None:
        return None, [], []

    pending: list[str] = []
    missing: list[str] = []
    for header, rows in tables:
        idx = next(
            (i for i, c in enumerate(header) if re.match(rf"^{code}(<br>|$)", c.strip())),
            None,
        )
        if idx is None:
            continue
        for row in rows:
            if len(row) <= idx:
                continue
            label = re.sub(r"[`*]", "", row[0]).strip()
            if PENDING_MARK in row[idx]:
                pending.append(label)
            elif MISSING_MARK in row[idx]:
                missing.append(label)
    return code, pending, missing


def detail(detail_dir: Path, filename: str, limit: int = 1500) -> str:
    f = detail_dir / filename
    if not f.exists():
        return ""
    text = f.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return ""
    if len(text) > limit:
        text = text[:limit] + "\n…（截斷，完整內容看 Actions log）"
    return text


def cross_platform_note(ubuntu: str | None, windows: str | None) -> str | None:
    """ci.yml 那段註解要的判讀：只有 Windows 壞 vs 兩邊都壞。"""
    if ubuntu is None or windows is None:
        return None
    u_ok, w_ok = ubuntu == "success", windows == "success"
    if u_ok and w_ok:
        return None
    if u_ok and not w_ok:
        return (
            "⚠️ **只有 Windows 紅 → 這是相容性問題，不是程式邏輯錯。**\n"
            "先查這三樣：檔案編碼（`cp950` vs `utf-8`）、行尾（CRLF vs LF）、"
            "路徑分隔符。#8 修過同一類問題，可以先看那個 PR 的 diff。"
        )
    if not u_ok and w_ok:
        return (
            "⚠️ **只有 Ubuntu 紅。** 少見，通常是檔名大小寫（Windows 不分、Linux 分）"
            "或只存在於 Linux 的路徑假設。"
        )
    return "❌ **兩邊都紅 → 程式邏輯真的有問題**，不是平台差異。先在本機重現。"


def build(results: dict[str, str], detail_dir: Path, author: str = "") -> str:
    guard = pick(results, "越界檢查")
    deps = pick(results, "相依同步")
    ubuntu = pick(results, "測試", "ubuntu")
    windows = pick(results, "測試", "windows")

    icon = {"success": "✅", "failure": "❌", "cancelled": "⚪", "skipped": "⚪"}
    rows = [
        ("越界檢查", guard),
        ("相依同步檢查", deps),
        ("測試（ubuntu）", ubuntu),
        ("測試（windows）", windows),
    ]

    lines = [MARKER, "## CI 結果", "", "| 檢查 | 結果 |", "|---|---|"]
    for label, conclusion in rows:
        if conclusion is None:
            continue
        lines.append(f"| {label} | {icon.get(conclusion, '⏳')} {conclusion} |")

    failed = [label for label, c in rows if c == "failure"]
    if not failed:
        lines += ["", "全部通過。這則留言只是報告，不影響合併。"]
        return "\n".join(lines + alignment_note(author))

    lines += ["", "---", ""]

    note = cross_platform_note(ubuntu, windows)
    if note:
        lines += [note, ""]

    if guard == "failure":
        # 這個 job 包兩種檢查，是完全不同的問題，建議也不一樣。
        # 不分開講的話，import 越界的人會看到「你動到別人資料夾」這種錯誤指引。
        boundaries = detail(detail_dir, "boundaries.txt")
        ownership = detail(detail_dir, "ownership.txt")
        cp950 = detail(detail_dir, "cp950.txt")

        if "[X]" in boundaries:
            lines += ["### 模組越界 import", "", "```", boundaries, "```", ""]
            lines += [
                "模組資料夾裡不准出現三樣東西：模型套件（torch、transformers…）、"
                "連網套件（requests、httpx…）、別人的模組。",
                "",
                "模型呼叫一律走 `shared.models`，五個人才會用到同一組模型；"
                "出網的口只有 `shared.models.call_cloud` 一個，原文才不會離開"
                "使用者的電腦。",
                "",
            ]

        if "[X]" in ownership:
            lines += ["### 改到別人的資料夾", "", "```", ownership, "```", ""]
            lines += [
                "五套程式互不相認 —— 需要別人改東西請開 Issue（§1.7），不要直接動他的資料夾。",
                "",
                "如果你覺得判斷錯了，八成是 `.github/team.yml` 裡你的帳號或 `module_dir` 沒填對。",
                "",
            ]

        if "[X]" in cp950:
            lines += ["### 跨平台輸出（繁中 Windows 會炸）", "", "```", cp950, "```", ""]
            lines += [
                "這些字元在 macOS 上沒事，在繁中 Windows（cp950）會 `UnicodeEncodeError`。"
                "本隊四台是 Windows，而且輸出一旦被接成管道（pre-commit、CI、"
                "`make eval > out.txt`）就會當場炸掉。",
                "",
                "換成 ASCII 即可：勾/叉 -> `[OK]`/`[X]`，大於等於 -> `>=`，小於等於 -> `<=`。"
                "Streamlit（`app/ui.py`）畫到瀏覽器不走 stdout，不受此限。",
                "",
            ]

        # 三個檔都沒有 [X] 卻紅了：job 本身出問題（裝不起來、逾時之類）
        if "[X]" not in boundaries and "[X]" not in ownership and "[X]" not in cp950:
            lines += [
                "### 越界檢查沒過",
                "",
                "檢查程式本身沒報錯，是 job 出了問題（依賴裝不起來、逾時之類）。"
                "點進 Actions 看完整 log。",
                "",
            ]

    if deps == "failure":
        lines += ["### 相依同步檢查沒過", ""]
        d = detail(detail_dir, "deps.txt")
        if d:
            lines += ["```", d, "```", ""]
        lines += [
            "`requirements.txt` 是手寫的 `uv.lock` 鏡像，動過 lock 就要跟著改，"
            "不然用 pip 的 Windows 那幾台會裝到跟 CI 不同的版本。",
            "",
        ]

    if ubuntu == "failure" or windows == "failure":
        lines += [
            "### 測試沒過",
            "",
            "在本機依序跑這三行，跟 CI 跑的是同一套：",
            "",
            "```bash",
            "uv run ruff check --fix .",
            "uv run ruff format .",
            "uv run pytest -q",
            "```",
            "",
            "沒裝 uv 的話（四台 Windows 大多是這種）：",
            "",
            "```bash",
            "python -m ruff check --fix . && python -m pytest -q",
            "```",
            "",
        ]

    lines += ["---", "", "紅燈**不會**擋住合併，這則留言只是提醒。"]
    return "\n".join(lines + alignment_note(author))


def alignment_note(author: str) -> list[str]:
    """還沒對齊環境的人，在他自己的 PR 上點名提醒。

    只點有待辦的人 —— 弄完的人不會被騷擾，這段就自動消失。這比開一個
    大家都會放著爛的 Issue 有效，因為它出現在他正在做事的地方。
    """
    if not author:
        return []
    code, pending, missing = pending_items(author)
    if not code or (not pending and not missing):
        return []

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    doc = "docs/環境對齊追蹤表.md"
    link = f"[`{doc}`]({server}/{repo}/blob/main/{doc})" if repo else f"`{doc}`"

    out = ["", "---", "", f"### ⚠️ 你的開發環境有待辦（{code} 欄）", ""]
    if missing:
        out += [f"**要補裝（{len(missing)}）**：" + " · ".join(missing), ""]
    if pending:
        out += [f"**未回報（{len(pending)}）**：" + " · ".join(pending), ""]
    out += [
        f"回報指令在 {link}，跑完改自己那一欄。全部弄完這段提醒就會自動消失。",
    ]
    return out


def upsert_comment(repo: str, token: str, pr: int, body: str) -> None:
    existing = api(f"/repos/{repo}/issues/{pr}/comments?per_page=100", token) or []
    for c in existing:
        if MARKER in (c.get("body") or ""):
            api(f"/repos/{repo}/issues/comments/{c['id']}", token, "PATCH", {"body": body})
            print(f"[OK] 更新既有留言 {c['id']}")
            return
    api(f"/repos/{repo}/issues/{pr}/comments", token, "POST", {"body": body})
    print("[OK] 新增留言")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--detail-dir", default="detail")
    ap.add_argument("--dry-run", action="store_true", help="只印出來，不真的留言")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("[X] 沒有 GITHUB_TOKEN", file=sys.stderr)
        return 1

    results = job_results(args.repo, token, args.run_id)
    pr = api(f"/repos/{args.repo}/pulls/{args.pr}", token)
    body = build(results, Path(args.detail_dir), pr["user"]["login"])

    if args.dry_run:
        print(body)
        return 0

    upsert_comment(args.repo, token, args.pr, body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
