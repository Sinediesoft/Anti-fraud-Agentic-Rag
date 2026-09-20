"""機器人檢查六：docs/model-lock.md 的鎖定表與程式裡的 MODEL_LOCK 有沒有走散。

docs/model-lock.md 自己就寫著「兩邊不一致會讓分數對不起來」—— 但在這支之前，
沒有任何東西在檢查它。手寫的兩份資料一定會走散，這跟 requirements.txt 對
uv.lock 是同一類問題，已經為那件事寫過 check_requirements_sync.py 了。

為什麼這件事比看起來嚴重：模型版本錯了不會噴錯，只會讓五個人的分數不能互比，
而那通常要到合併週才發現。文件上寫 A 版、程式裡跑 B 版，兩邊都「看起來對」。

比對四個欄位：模型名、版本編號、壓縮格式、鎖定日期。

版本編號在表格裡可以縮寫（例：5617a9f6…aefb181），那是為了排版。縮寫會被
當成「頭尾必須對得上完整值」來驗，不是放寬 —— 寫錯的縮寫一樣抓得到。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "model-lock.md"

sys.path.insert(0, str(REPO_ROOT / "packages"))
from shared.models import MODEL_LOCK  # noqa: E402

# 文件的中文用途名 -> MODEL_LOCK 的鍵
PURPOSE_KEYS = {
    "地端小模型（SLM）": "slm",
    "嵌入模型": "embedding",
    "重排序模型": "reranker",
    "雲端模型": "cloud",
}

ELLIPSIS = "…"


def clean(cell: str) -> str:
    """去掉 markdown 的粗體與行內程式碼標記。"""
    return cell.replace("**", "").replace("`", "").strip()


def lock_table_rows() -> dict[str, list[str]]:
    """抓鎖定表，回傳 {用途: [模型名, 版本編號, 壓縮格式, 鎖定日期]}。"""
    rows: dict[str, list[str]] = {}
    in_table = False
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("| 用途 |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [clean(c) for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            if len(cells) < 5 or set(cells[0]) <= set("-: "):
                continue
            rows[cells[0]] = cells[1:5]
    return rows


def revision_matches(doc_rev: str, code_rev: str) -> bool:
    """文件的版本編號可以縮寫，但頭尾一定要對得上完整值。"""
    if ELLIPSIS not in doc_rev:
        return doc_rev == code_rev
    head, _, tail = doc_rev.partition(ELLIPSIS)
    return (
        code_rev.startswith(head) and code_rev.endswith(tail) and len(code_rev) > len(head + tail)
    )


def main() -> int:
    if not DOC.exists():
        print(f"[!]  找不到 {DOC.name}，跳過檢查")
        return 0

    rows = lock_table_rows()
    problems: list[str] = []

    for purpose, key in PURPOSE_KEYS.items():
        lock = MODEL_LOCK[key]
        if purpose not in rows:
            problems.append(f"[X] 鎖定表缺少「{purpose}」那一列")
            continue
        name, rev, quant, locked_on = rows[purpose]

        # 文件寫 TODO 的，程式那邊也必須是未鎖定，反之亦然
        doc_todo = name.upper().startswith("TODO")
        if doc_todo != (not lock.is_locked):
            state = "已鎖定" if lock.is_locked else "未鎖定"
            doc_state = "TODO" if doc_todo else f"已填 {name}"
            problems.append(
                f"[X] {purpose}：文件寫「{doc_state}」，但程式裡是{state}（{lock.name}）"
            )
            continue
        if doc_todo:
            continue

        for label, doc_val, code_val in (
            ("模型名", name, lock.name),
            ("壓縮格式", quant, lock.quantization),
            ("鎖定日期", locked_on, lock.locked_on),
        ):
            if doc_val != code_val:
                problems.append(f"[X] {purpose} 的{label}：文件「{doc_val}」vs 程式「{code_val}」")
        if not revision_matches(rev, lock.revision):
            problems.append(f"[X] {purpose} 的版本編號：文件「{rev}」對不上程式「{lock.revision}」")

    if not problems:
        locked = sum(1 for v in MODEL_LOCK.values() if v.is_locked)
        print(f"[OK] 模型鎖定表與 MODEL_LOCK 一致（{len(PURPOSE_KEYS)} 項，已鎖定 {locked} 項）")
        return 0

    for p in problems:
        print(p)
    print(
        "\ndocs/model-lock.md 的鎖定表與 packages/shared/models.py 的 MODEL_LOCK\n"
        "必須一致。兩邊不一致不會噴錯，只會讓五個人的分數不能互比 —— 而那通常\n"
        "要到合併週才發現。改動任一邊時，另一邊要跟著改。"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
