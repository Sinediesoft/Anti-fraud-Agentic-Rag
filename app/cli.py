"""指令列入口。Makefile 的 eval / index / selfcheck 都走這裡。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from contracts import AnalyzeInput

from . import guards
from .registry import MODULES_ROOT, load

OK = "✅"
NO = "❌"
WARN = "⚠️ "


def _load_one(module_id: str):
    registry = load(module_id)
    if registry.failures:
        for f in registry.failures:
            print(f"{NO} {f.module_id}：{f.reason}")
        return None
    return registry.get(module_id)


# ──────────────────────────────────────────────────────────────
# selfcheck：S16 入場檢查七項
# ──────────────────────────────────────────────────────────────


def cmd_selfcheck(module_id: str) -> int:
    print(f"── 入場檢查：{module_id}（說明書 S16）──\n")
    results: list[tuple[bool, str]] = []
    path = MODULES_ROOT / module_id

    loaded = _load_one(module_id)
    results.append((loaded is not None, "1. 四個進入點都實作，pack.yaml 載得起來"))
    if loaded is None:
        _print_results(results)
        return 1

    pack = loaded.pack

    # 2. pack.yaml 裡提到的每個檔案都存在
    missing = [
        str(rel)
        for rel in pack.deliverables.values()
        if isinstance(rel, str)
        and rel.endswith((".py", ".yaml", ".md", ".jsonl"))
        and not (path / rel).exists()
    ]
    results.append(
        (
            not missing,
            f"2. pack.yaml 提到的檔案都存在{'：缺 ' + ', '.join(missing) if missing else ''}",
        )
    )

    # 3. 自己的 20 題考題，can_handle 至少 17 題高於門檻
    questions_file = path / "eval" / "route_questions.yaml"
    if questions_file.exists():
        questions = yaml.safe_load(questions_file.read_text(encoding="utf-8")) or []
        hits = sum(
            1
            for q in questions
            if loaded.instance.can_handle(AnalyzeInput(text=q.get("text", "")))
            >= pack.thresholds.route_min
        )
        total = len(questions)
        results.append(
            (total >= 20 and hits >= 17, f"3. 20 題考題中 {hits}/{total} 題認得出來（要 ≥ 17/20）")
        )
    else:
        results.append((False, "3. 還沒有 eval/route_questions.yaml（S12 第 4 點要出 20 題）"))

    # 4. 每個階段都有行動，高風險階段不是空的
    playbook_file = path / "playbook.yaml"
    if playbook_file.exists():
        book = yaml.safe_load(playbook_file.read_text(encoding="utf-8")) or {}
        stages = book.get("stages", []) or []
        empty = [s.get("id", "?") for s in stages if not s.get("actions")]
        severe_empty = [
            s.get("id", "?")
            for s in stages
            if s.get("risk") in ("high", "critical") and not s.get("actions")
        ]
        ok = bool(stages) and not severe_empty
        detail = f"：{len(stages)} 個階段"
        if empty:
            detail += f"，空的：{', '.join(empty)}"
        results.append((ok, f"4. 每階段都有行動、高風險不得為空{detail}"))
    else:
        results.append((False, "4. 還沒有 playbook.yaml（S14 第 4 點）"))

    # 5. 能單獨跑完一次（冒煙測試）
    try:
        verdict = loaded.instance.analyze(AnalyzeInput(text="我在網路上被騙了，已經匯了三萬元出去"))
        problems = guards.check(verdict)
        fatal = [p for p in problems if p.fatal]
        results.append(
            (not fatal, f"5. 能跑完一次並通過輸出檢核{'：' + fatal[0].detail if fatal else ''}")
        )
    except Exception as exc:
        results.append((False, f"5. 跑不完：{type(exc).__name__}: {exc}"))

    # 6 + 7. 越界檢查
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.check_boundaries import scan_module  # noqa: PLC0415

    violations = scan_module(path)
    results.append(
        (
            not violations,
            f"6+7. 沒有自己寫共用三樣、沒有 import 別人的模組"
            f"{'：' + violations[0].message if violations else ''}",
        )
    )

    return _print_results(results)


def _print_results(results: list[tuple[bool, str]]) -> int:
    for ok, label in results:
        print(f"  {OK if ok else NO} {label}")
    failed = sum(1 for ok, _ in results if not ok)
    print()
    if failed:
        print(f"{NO} 還有 {failed} 項沒過。七項全過才准掛載（S16）。")
        return 1
    print(f"{OK} 七項全過，可以掛載。")
    return 0


# ──────────────────────────────────────────────────────────────
# eval / index
# ──────────────────────────────────────────────────────────────


def cmd_eval(module_id: str) -> int:
    loaded = _load_one(module_id)
    if loaded is None:
        return 1
    runner = getattr(__import__(f"modules.{module_id}.evaluate", fromlist=["run"]), "run", None)
    if runner is None:
        print(f"{WARN}{module_id} 還沒有 evaluate.run()。S15 之前會是這樣。")
        return 0
    scores = runner()
    print(json.dumps(scores, ensure_ascii=False, indent=2))
    return 0


def cmd_index(module_id: str) -> int:
    loaded = _load_one(module_id)
    if loaded is None:
        return 1
    builder = getattr(
        __import__(f"modules.{module_id}.m3_retrieval", fromlist=["build_index"]),
        "build_index",
        None,
    )
    if builder is None:
        print(f"{WARN}{module_id} 還沒有 m3_retrieval.build_index()。S12 才會做。")
        return 0
    builder()
    return 0


def cmd_list() -> int:
    registry = load("all")
    print("── 已載入的模組 ──")
    for m in registry.loaded:
        combo = (
            f"{m.pack.platform} × {m.pack.tactic}"
            if m.pack.is_configured
            else "（平台 × 手法未設定）"
        )
        state = OK if m.usable else WARN
        print(f"  {state} {m.id:<12} {m.pack.name:<20} {m.pack.plan.value:<5} {combo}")
    for f in registry.failures:
        print(f"  {NO} {f.module_id}：{f.reason}")
    if not registry.loaded and not registry.failures:
        print("  （還沒有任何模組。_template 要指名才會載入）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="afc", description="防詐 Copilot 指令列")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, help_text in [
        ("selfcheck", "S16 入場檢查七項"),
        ("eval", "算分數"),
        ("index", "建向量庫"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--module", required=True)

    sub.add_parser("list", help="列出載入的模組")

    args = parser.parse_args(argv)
    if args.cmd == "selfcheck":
        return cmd_selfcheck(args.module)
    if args.cmd == "eval":
        return cmd_eval(args.module)
    if args.cmd == "index":
        return cmd_index(args.module)
    return cmd_list()


if __name__ == "__main__":
    raise SystemExit(main())
