"""機器人檢查一：PR 有沒有改到不該他改的資料夾（說明書 S1 第 5 點）。

規則很簡單：你不能改別人的模組資料夾。共用層（contracts / shared / app）
大家都能改，但 CODEOWNERS 會要求全員審 —— 那是另一道關卡。

.github/team.yml 裡的帳號還是 TODO 時，這支只會提醒不會擋，
免得 S1 還沒做完就讓所有 PR 都紅燈。

## 整合型 PR（2026-09-22 補）

`--author` 拿到的是**開 PR 的人**（CI 傳 pull_request.user.login），原本的
判斷只看「路徑屬於誰」，完全不看 commit 是誰寫的。那對「一個人一個模組」
的 PR 是對的，但整合分支的定義就是同時帶著多個人的模組資料夾 ——
PR #42 帶著 @a24209422 自己寫的 11 個 c_tbd commit，照原本的規則會報 16 個
錯，而那裡面沒有一個是真的越界。

所以多收一個 `--range`：外模組的檔案先查它在這個範圍內的 commit 作者，
**全部都是該資料夾的主人**才降級成提醒，否則照樣是錯誤。

刻意 fail closed —— 查不到作者、git 不能用、沒給 `--range` 時一律維持
原本的錯誤。工具的盲點讓人多解釋一次還好，放行一次真的越界就不好了。
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TEAM_FILE = REPO_ROOT / ".github" / "team.yml"
MODULES_PREFIX = "packages/modules/"


def load_team() -> dict:
    if not TEAM_FILE.exists():
        return {}
    return yaml.safe_load(TEAM_FILE.read_text(encoding="utf-8")) or {}


def _authors_of(path: str, rev_range: str) -> list[str] | None:
    """這個範圍內動過 path 的 commit 作者，回 "名字 <email>"。查不出來回 None。"""
    try:
        out = subprocess.run(
            ["git", "log", "--format=%an <%ae>", rev_range, "--", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    seen = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return seen or None


def _is_owner(author: str, member: dict) -> bool:
    """這個 git 作者是不是 team.yml 裡的這個人。

    GitHub 建立的 commit email 是 `<數字>+<帳號>@users.noreply.github.com`，
    帳號就在裡面，這是最可靠的一條。但有人用自己的真實 email（本機 git
    config），那條認不出來 —— 所以再退一步比對 team.yml 的 name 欄位。
    兩條都對不上就當作認不出來，交給呼叫端 fail closed。
    """
    login = str(member.get("github", "")).lower()
    if not login or login.startswith("todo"):
        return False
    low = author.lower()
    email = low.split("<")[-1].rstrip(">") if "<" in low else ""
    if email and re.fullmatch(rf"(?:\d+\+)?{re.escape(login)}@users\.noreply\.github\.com", email):
        return True
    if email.split("@")[0] == login:
        return True
    name = str(member.get("name", "")).strip().lower()
    return bool(name) and low.split("<")[0].strip() == name


def _attributed_to_owner(path: str, owner: dict | None, rev_range: str | None) -> str | None:
    """這個檔案在這次 PR 裡是不是「主人自己改的」。是就回一句提醒，否則回 None。

    整合型 PR 才走得到這裡。任何一步答不出來都回 None（-> 維持錯誤）。
    """
    if owner is None or not rev_range:
        return None
    authors = _authors_of(path, rev_range)
    if not authors:
        return None
    if not all(_is_owner(a, owner) for a in authors):
        return None
    return f"[i]  {path} 不是你的資料夾，但這次的改動都出自主人 @{owner.get('github')} —— 整合型 PR，放行。"


def check(
    author: str, changed: list[str], rev_range: str | None = None
) -> tuple[list[str], list[str]]:
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
            owner = next(
                (
                    m
                    for m in configured
                    if str(m.get("module_dir", "")).rstrip("/") + "/" == owner_dir
                ),
                None,
            )
            note = _attributed_to_owner(f, owner, rev_range)
            if note:
                notes.append(note)
                continue
            errors.append(f"[X] {f} 不是你的模組資料夾（你的是 {my_dir}）")
        elif any(f.startswith(p) for p in team.get("shared_paths", [])):
            notes.append(f"[i]  {f} 是共用層，凍結後只准修 bug，而且要全員審。")

    return errors, notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--author", required=True)
    parser.add_argument("--changed-files", required=True, help="一行一個檔名的檔案")
    parser.add_argument(
        "--range",
        dest="rev_range",
        default=None,
        help="比對 commit 作者用的範圍，例：origin/main...HEAD。"
        "不給就退回原本的規則（外模組一律算錯），需要完整歷史（fetch-depth: 0）",
    )
    args = parser.parse_args()

    changed = Path(args.changed_files).read_text(encoding="utf-8").splitlines()
    errors, notes = check(args.author, changed, args.rev_range)

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
