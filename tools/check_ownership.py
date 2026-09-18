"""機器人檢查一：PR 有沒有改到不該他改的資料夾（說明書 S1 第 5 點）。

規則很簡單：你不能改別人的模組資料夾。共用層（contracts / shared / app）
大家都能改，但 CODEOWNERS 會要求全員審 —— 那是另一道關卡。

.github/team.yml 裡的帳號還是 TODO 時，這支只會提醒不會擋，
免得 S1 還沒做完就讓所有 PR 都紅燈。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TEAM_FILE = REPO_ROOT / ".github" / "team.yml"
MODULES_PREFIX = "packages/modules/"


def load_team() -> dict:
    if not TEAM_FILE.exists():
        return {}
    return yaml.safe_load(TEAM_FILE.read_text(encoding="utf-8")) or {}


def check(author: str, changed: list[str]) -> tuple[list[str], list[str]]:
    team = load_team()
    members = team.get("members", []) or []
    configured = [m for m in members if not str(m.get("github", "")).startswith("TODO")]

    if not configured:
        return [], ["[!]  .github/team.yml 的 GitHub 帳號還沒填（S1 第 3 步），這次只提醒不擋。"]

    me = next((m for m in configured if m.get("github", "").lower() == author.lower()), None)
    if me is None:
        return [], [f"[!]  {author} 不在 .github/team.yml 裡，跳過檢查。"]

    my_dir = str(me.get("module_dir", "")).rstrip("/") + "/"
    errors: list[str] = []
    notes: list[str] = []

    for f in changed:
        f = f.strip()
        if not f:
            continue
        if f.startswith(MODULES_PREFIX) and not f.startswith(my_dir):
            owner_dir = "/".join(f.split("/")[:3]) + "/"
            if owner_dir.rstrip("/").split("/")[-1].startswith("_"):
                notes.append(f"[i]  {f} 屬於範本模組，W2 之後應該只有 bug 修正。")
                continue
            errors.append(f"[X] {f} 不是你的模組資料夾（你的是 {my_dir}）")
        elif any(f.startswith(p) for p in team.get("shared_paths", [])):
            notes.append(f"[i]  {f} 是共用層，凍結後只准修 bug，而且要全員審。")

    return errors, notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--author", required=True)
    parser.add_argument("--changed-files", required=True, help="一行一個檔名的檔案")
    args = parser.parse_args()

    changed = Path(args.changed_files).read_text(encoding="utf-8").splitlines()
    errors, notes = check(args.author, changed)

    for n in notes:
        print(n)
    for e in errors:
        print(e)

    if errors:
        print("\n五套程式互不相認 —— 需要別人改東西請開 Issue（§1.7），不要直接動他的資料夾。")
        return 1
    print("[OK] 修改範圍檢查通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
