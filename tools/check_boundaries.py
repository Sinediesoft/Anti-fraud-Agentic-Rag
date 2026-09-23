"""機器人檢查二：模組有沒有越界（說明書 S1 第 5 點）。

這支程式是「三樣東西不准自己寫」這條規矩唯一的執法者 ——
靠口頭約定一定守不住。

會擋的四件事：
  1. 模組 import 模型套件（torch、transformers、ollama、openai…）
     → 模型呼叫一律走 shared.models，五個人才會用同一組模型
  2. 模組 import 連網套件（requests、httpx、socket…）
     → 原文不離開使用者的電腦，出網的口只有 shared.models.call_cloud 一個
  3. 模組 import 別人的模組
     → 五套程式互不相認，合併時才撞不到彼此
  4. 模組自己實作去識別化或分數計算
     → 個資標準不能有五種，算法不同分數就不能比

用法：python tools/check_boundaries.py [模組資料夾…]
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULES_ROOT = REPO_ROOT / "packages" / "modules"

# 直接呼叫模型的套件。要用模型請走 shared.models
MODEL_PACKAGES = {
    "torch",
    "transformers",
    "sentence_transformers",
    "ollama",
    "llama_cpp",
    "openai",
    "anthropic",
    "google",
    "vertexai",
    "cohere",
    "litellm",
    "vllm",
    "ctransformers",
    "optimum",
    "onnxruntime",
    # OCR 引擎（2026-09-23 起認字走 shared.models.ocr）。原本只擋 onnxruntime，
    # 但 import rapidocr 會過 —— 它底層跑的就是 onnxruntime。這裡只看 import 的
    # 最外層名稱，抓不到間接相依，所以引擎套件要自己列出來。
    "rapidocr",
    "rapidocr_onnxruntime",
    "paddleocr",
    "paddle",
    "paddlex",
    "pytesseract",
    "tesserocr",
    "easyocr",
}

# 會連外網的套件。出網的口只有 shared.models 一個
NETWORK_PACKAGES = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "socket",
    "http",
    "ftplib",
    "telnetlib",
    "smtplib",
    "websockets",
    "boto3",
    "google_auth",
}

# 分數計算一律走 shared.eval
SCORING_PACKAGES = {"sklearn", "torchmetrics", "evaluate", "seqeval"}

# 自己重寫共用三樣的徵兆：定義了這些名字的函式
FORBIDDEN_FUNCTION_NAMES = {
    "mask": "去識別化請用 shared.deid.mask()",
    "deid": "去識別化請用 shared.deid",
    "anonymize": "去識別化請用 shared.deid",
    "redact": "去識別化請用 shared.deid",
    "macro_f1": "分數計算請用 shared.eval.macro_f1()",
    "recall_at_k": "分數計算請用 shared.eval.recall_at_k()",
    "mrr_at_k": "分數計算請用 shared.eval.mrr_at_k()",
    "cohen_kappa": "分數計算請用 shared.eval.cohen_kappa()",
    "cer": "分數計算請用 shared.eval.cer()",
}


@dataclass(frozen=True)
class Violation:
    file: Path
    line: int
    message: str

    def __str__(self) -> str:
        rel = self.file.relative_to(REPO_ROOT) if self.file.is_relative_to(REPO_ROOT) else self.file
        return f"{rel}:{self.line}  {self.message}"


def _root_package(name: str) -> str:
    return name.split(".")[0]


def _check_import(
    node: ast.Import | ast.ImportFrom, own_module: str, path: Path
) -> list[Violation]:
    names: list[str] = []
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif node.module and node.level == 0:
        names = [node.module]

    out: list[Violation] = []
    for name in names:
        root = _root_package(name)
        if root in MODEL_PACKAGES:
            out.append(
                Violation(path, node.lineno, f"不准 import {name} —— 模型呼叫請用 shared.models")
            )
        elif root in NETWORK_PACKAGES:
            out.append(
                Violation(
                    path,
                    node.lineno,
                    f"不准 import {name} —— 模組不連網，出網走 shared.models.call_cloud",
                )
            )
        elif root in SCORING_PACKAGES:
            out.append(
                Violation(path, node.lineno, f"不准 import {name} —— 分數計算請用 shared.eval")
            )
        elif root == "modules":
            other = name.split(".")[1] if "." in name else ""
            if other and other != own_module:
                out.append(Violation(path, node.lineno, f"不准 import 別人的模組 modules.{other}"))
    return out


def _check_functions(tree: ast.AST, path: Path) -> list[Violation]:
    out: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            hint = FORBIDDEN_FUNCTION_NAMES.get(node.name)
            if hint:
                out.append(Violation(path, node.lineno, f"不准自己定義 {node.name}() —— {hint}"))
    return out


def scan_file(path: Path, own_module: str) -> list[Violation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 0, f"語法錯誤：{exc.msg}")]

    out: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            out.extend(_check_import(node, own_module, path))
    out.extend(_check_functions(tree, path))
    return out


def scan_module(module_dir: Path) -> list[Violation]:
    """掃一個模組資料夾。app/cli.py 的 selfcheck 也呼叫這支。"""
    out: list[Violation] = []
    for py in sorted(module_dir.rglob("*.py")):
        out.extend(scan_file(py, module_dir.name))
    return out


def main(argv: list[str]) -> int:
    targets = (
        [Path(a).resolve() for a in argv]
        if argv
        else (
            [d for d in sorted(MODULES_ROOT.iterdir()) if d.is_dir()]
            if MODULES_ROOT.exists()
            else []
        )
    )

    violations: list[Violation] = []
    for target in targets:
        if target.is_dir():
            violations.extend(scan_module(target))
        elif target.suffix == ".py":
            violations.extend(scan_file(target, target.parent.name))

    if violations:
        print("[X] 越界檢查沒過：\n")
        for v in violations:
            print(f"   {v}")
        print(
            "\n規矩一：去識別化、模型呼叫、分數計算一律用 packages/shared/ 裡的共用函式。"
            "\n規矩二：你的模組不准 import 別人的模組。"
        )
        return 1

    print(f"[OK] 越界檢查通過（掃了 {len(targets)} 個模組）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
