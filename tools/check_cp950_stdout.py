"""機器人檢查五：印到 stdout 的字，繁中 Windows 編得出來嗎。

問題的形狀（Issue #7 就是這個）：

    mark = "✅" if row.passed else "❌"
    print(f"  {mark} ...")

macOS 的 locale 是 UTF-8，這段永遠沒事。繁中 Windows 的 locale 是 cp950，
而 cp950 編不出 U+2705(✅)、U+274C(❌)、U+2265(≥)、U+2264(≤) —— 輸出一旦
被接成管道（pre-commit、CI、make eval > out.txt 都是），當場 UnicodeEncodeError。

本隊五個人裡四個用 Windows，而模組 A 在 macOS 上開發 —— 寫的人永遠踩不到，
用的人全部踩到。這種不對稱正是它會活到合併週的原因。

為什麼用 AST 而不是 grep：

    grep 會把 app/ui.py 的 18 個 emoji 全部誤報 —— 那些是 Streamlit 畫到
    瀏覽器的，不走 stdout，完全沒問題。註解與 docstring 同理。
    只有真的被 print() 或 sys.stdout.write() 吃進去的字串才算數。

PYTHONUTF8=1 可以繞過，而且說明書補充也要求 Windows 組員設。但那是每台機器
各自的設定，靠它等於把正確性押在「五個人都記得設」上。原始碼不要有這些字元
才是真的擋得住。
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".ruff_cache", ".pytest_cache"}

# 走 stdout 的呼叫。sys.stderr 不列入 —— 它同樣會炸，但通常是給人看的
# 錯誤訊息，而且 CI 的 log 不會因此中斷整條管道。
STDOUT_CALLS = {"print"}
STDOUT_ATTRS = {("sys", "stdout", "write"), ("sys", "stdout", "writelines")}


def unencodable(text: str) -> list[str]:
    """這串字裡有哪些字元 cp950 編不出來。"""
    out = []
    for ch in text:
        try:
            ch.encode("cp950")
        except UnicodeEncodeError:
            if ch not in out:
                out.append(ch)
    return out


def _attr_chain(node: ast.AST) -> tuple[str, ...]:
    """把 sys.stdout.write 這種鏈拆成 ('sys','stdout','write')。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def is_stdout_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in STDOUT_CALLS
    if isinstance(node.func, ast.Attribute):
        return _attr_chain(node.func) in STDOUT_ATTRS
    return False


def strings_in(node: ast.AST) -> list[tuple[int, str]]:
    """這個節點底下所有字串常數（含 f-string 的固定片段）與其行號。"""
    return [
        (getattr(sub, "lineno", 0), sub.value)
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    ]


def string_vars(scope: ast.AST) -> dict[str, list[tuple[int, str]]]:
    """這個範圍內「變數 -> 它被指派過的字串常數」。

    Issue #7 的形狀正是這個，只看 print() 內部的字面值會整個漏掉：

        mark = "<勾>" if row.passed else "<叉>"     <- 字元在這裡
        print(f"  {mark} ...")                      <- print 裡沒有字面值

    不做完整的資料流分析，只認同一個函式內的簡單指派 —— 實務上會出問題的
    就是這種寫法，多做只會增加誤報。
    """
    out: dict[str, list[tuple[int, str]]] = {}
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found = strings_in(node.value)
                if found:
                    out.setdefault(target.id, []).extend(found)
    return out


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """回傳 [(行號, 編不出的字元, 原字串)]。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        print(f"[!]  {path} 解析失敗（{e.msg}），跳過")
        return []

    # 每個函式一張表，再加上模組層級的 —— 足以涵蓋實務上的寫法
    scopes = [tree] + [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    var_strings: dict[str, list[tuple[int, str]]] = {}
    for scope in scopes:
        for name, vals in string_vars(scope).items():
            var_strings.setdefault(name, []).extend(vals)

    hits: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and is_stdout_call(node)):
            continue

        candidates = list(strings_in(node))
        # 再把 print 裡引用到的變數展開
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                candidates.extend(var_strings.get(sub.id, []))

        for lineno, text in candidates:
            chars = unencodable(text)
            if chars and (lineno, text) not in seen:
                seen.add((lineno, text))
                hits.append((lineno, "".join(chars), text.strip()[:60]))
    return sorted(hits)


def targets(paths: list[str]) -> list[Path]:
    if paths:
        return [Path(p) for p in paths if p.endswith(".py")]
    return [f for f in sorted(REPO_ROOT.rglob("*.py")) if not SKIP_DIRS.intersection(f.parts)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", help="要檢查的檔案；不給就掃全 repo")
    args = ap.parse_args()

    files = targets(args.paths)
    problems = {f: hits for f in files if (hits := scan_file(f))}

    if not problems:
        print(f"[OK] 跨平台輸出檢查通過（掃了 {len(files)} 個檔）")
        return 0

    for f, hits in problems.items():
        rel = f.relative_to(REPO_ROOT) if f.is_absolute() else f
        for lineno, chars, text in hits:
            print(f"[X] {rel}:{lineno}  印出 [{chars}] —— 繁中 Windows(cp950) 編不出來")
            print(f"       {text}")

    # 這段本身也得 cp950 安全 —— 不然這支程式會在它要保護的平台上自己炸掉。
    # 所以用代碼點描述，不寫出字元本身。
    print(
        "\n這些字元在 macOS 上沒事，在繁中 Windows 會 UnicodeEncodeError，\n"
        "而本隊四台是 Windows。請換成 ASCII：\n"
        "\n"
        "    U+2705 白色勾    -> [OK]      U+274C 紅叉      -> [X]\n"
        "    U+2265 大於等於  -> >=        U+2264 小於等於  -> <=\n"
        "    U+26A0 警告      -> [!]       U+2192 右箭頭    -> ->\n"
        "\n"
        "只有 print() 與 sys.stdout.write() 會被檢查。Streamlit（app/ui.py）\n"
        "畫到瀏覽器不走 stdout，愛用幾個 emoji 都行。"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
