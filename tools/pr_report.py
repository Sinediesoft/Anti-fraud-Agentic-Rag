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
import sys
import urllib.request
from pathlib import Path

API = "https://api.github.com"
MARKER = "<!-- claude-pr-report -->"


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


def build(results: dict[str, str], detail_dir: Path) -> str:
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
        return "\n".join(lines)

    lines += ["", "---", ""]

    note = cross_platform_note(ubuntu, windows)
    if note:
        lines += [note, ""]

    if guard == "failure":
        lines += ["### 越界檢查沒過", ""]
        d = detail(detail_dir, "guard.txt")
        if d:
            lines += ["```", d, "```", ""]
        lines += [
            "你動到了不屬於你的模組資料夾。五套程式互不相認 —— 需要別人改東西"
            "請開 Issue（§1.7），不要直接動他的資料夾。",
            "",
            "如果你覺得判斷錯了，八成是 `.github/team.yml` 裡你的帳號或 `module_dir` 沒填對。",
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
    return "\n".join(lines)


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
    body = build(results, Path(args.detail_dir))

    if args.dry_run:
        print(body)
        return 0

    upsert_comment(args.repo, token, args.pr, body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
