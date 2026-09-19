"""機器人檢查四：requirements.txt 有沒有跟 uv.lock 走散。

為什麼需要這支：requirements.txt 是**手寫**的鏡像（見它自己的檔頭），
專案的權威是 pyproject.toml ＋ uv.lock。手寫的東西一定會忘記更新，
而忘記的後果很具體 —— 用 uv 的人（CI 與 macOS）跟用 pip 的人
（四台 Windows）裝到不同版本，然後就是「我這邊明明可以跑」。

查兩件事：

    1. 版本漂移：requirements.txt 釘的版本 != uv.lock 解出來的版本
    2. 缺漏：pyproject 的 core / dev / ui 直接相依沒出現在 requirements.txt

不查「多餘」—— requirements.txt 多列東西通常是刻意的，不該報錯。
也不查 data extra，因為 requirements.txt 的檔頭寫明它只鏡像 dev + ui。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQ = REPO_ROOT / "requirements.txt"
LOCK = REPO_ROOT / "uv.lock"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# requirements.txt 的檔頭說它鏡像這幾組
MIRRORED_EXTRAS = ("dev", "ui")


def norm(name: str) -> str:
    """PEP 503：大小寫不分，- _ . 一律當成 -。PyYAML 與 pyyaml 是同一個。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def dep_name(spec: str) -> str:
    """從 'pydantic>=2.7' 或 'httpx[http2]>=0.27; python_version<\"3.12\"' 取名字。"""
    return norm(re.split(r"[\[<>=!~;\s]", spec.strip(), maxsplit=1)[0])


def read_lock() -> dict[str, str]:
    data = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    return {norm(p["name"]): p["version"] for p in data.get("package", []) if "version" in p}


def read_requirements() -> tuple[dict[str, str], list[str]]:
    """回傳 {正規化名稱: 釘選版本} 與看不懂的行。"""
    pinned: dict[str, str] = {}
    odd: list[str] = []
    for raw in REQ.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)\s*==\s*([^\s;]+)", line)
        if m:
            pinned[norm(m.group(1))] = m.group(2)
        else:
            odd.append(line)
    return pinned, odd


def read_direct_deps() -> dict[str, str]:
    """pyproject 的 core + 被鏡像的 extras，回傳 {正規化名稱: 原始寫法}。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data.get("project", {})
    specs = list(project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    for extra in MIRRORED_EXTRAS:
        specs.extend(optional.get(extra, []))
    return {dep_name(s): s for s in specs}


def main() -> int:
    for f in (REQ, LOCK, PYPROJECT):
        if not f.exists():
            print(f"[!]  找不到 {f.name}，跳過檢查")
            return 0

    lock = read_lock()
    pinned, odd = read_requirements()
    direct = read_direct_deps()

    drifted: list[str] = []
    missing: list[str] = []
    unknown: list[str] = []

    for name, version in sorted(pinned.items()):
        if name not in lock:
            unknown.append(f"{name}=={version}")
        elif lock[name] != version:
            drifted.append(f"{name}：requirements.txt 釘 {version}，uv.lock 解出 {lock[name]}")

    for name, spec in sorted(direct.items()):
        if name not in pinned:
            missing.append(f"{spec}（pyproject 有，requirements.txt 沒有）")

    for label, items in (("看不懂的行", odd), ("不在 uv.lock 裡", unknown)):
        for i in items:
            print(f"[i]  {label}：{i}")

    if not drifted and not missing:
        print(f"[OK] requirements.txt 與 uv.lock 同步（比對了 {len(pinned)} 個套件）")
        return 0

    for d in drifted:
        print(f"[X] {d}")
    for m in missing:
        print(f"[X] {m}")

    extras = " ".join(f"--extra {e}" for e in MIRRORED_EXTRAS)
    print(
        "\nrequirements.txt 是手寫鏡像，uv.lock 改過就要跟著改。重生一份：\n"
        f"    uv export --no-hashes {extras} -o requirements.txt\n"
        "或手動把上面幾行的版本對齊。不修的話，用 pip 的 Windows 那幾台\n"
        "會裝到跟 CI 不同的版本。"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
