"""地端 SLM 實測（S3 / S13）。

關鍵問題不是「跑不跑得動」，而是「有沒有真的在 GPU 上」——
Ollama 在 VRAM 不足時會無聲把部分層丟回 CPU，不報錯，只是變慢。
所以一定要看 `ollama ps` 的 PROCESSOR 欄。

取樣參數對齊 packages/shared/models.py 的 MODEL_LOCK 常數。

用法（只需要 Ollama 已啟動，不需要 torch）：
    uv run --no-project --python 3.11 python tools/bench/slm_latency.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _corpus import FEW_SHOT  # noqa: E402

HOST = "http://127.0.0.1:11434"
MODEL = "qwen2.5:3b"

# 對齊 shared/models.py 的 TEMPERATURE / TOP_P / SEED
OPTS = {"temperature": 0.0, "top_p": 1.0, "seed": 20260918}

SYSTEM = "你是反詐騙判讀助理。根據使用者的敘述，判斷詐騙類型與目前進展階段，並以繁體中文簡潔回答。"


def build_prompt(n_examples: int, question: str) -> str:
    parts = [SYSTEM, ""]
    for q, kind, stage in FEW_SHOT[:n_examples]:
        parts.append(f"敘述：{q}\n類型：{kind}\n階段：{stage}\n")
    parts.append(f"敘述：{question}\n類型：")
    return "\n".join(parts)


def generate(prompt: str, num_predict: int, num_ctx: int | None = None) -> dict:
    opts = {**OPTS, "num_predict": num_predict}
    if num_ctx:
        opts["num_ctx"] = num_ctx
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": True, "options": opts}).encode()
    req = urllib.request.Request(
        HOST + "/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    ttft, final = None, {}
    with urllib.request.urlopen(req, timeout=600) as resp:
        for line in resp:
            if not line.strip():
                continue
            d = json.loads(line)
            if ttft is None and d.get("response"):
                ttft = (time.perf_counter() - t0) * 1000
            if d.get("done"):
                final = d
    return {
        "ttft": ttft or 0.0,
        "prompt_tok": final.get("prompt_eval_count", 0),
        "gen_tok": final.get("eval_count", 0),
        "gen_ms": final.get("eval_duration", 0) / 1e6,
        "total_ms": final.get("total_duration", 0) / 1e6,
    }


def ollama_ps() -> tuple[str, str]:
    """回傳 (模型佔用, PROCESSOR)。PROCESSOR 是重點：100% GPU 還是有 CPU 分流。"""
    out = subprocess.run(
        ["ollama", "ps"], capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout
    for ln in out.splitlines()[1:]:
        if MODEL.split(":")[0] in ln:
            m = re.search(r"(\d+(?:\.\d+)?\s*[GM]B)\s+(\d+%\s*\S+(?:/\S+)?)", ln)
            if m:
                return m.group(1), m.group(2)
    return "?", "?"


def nvidia() -> str:
    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout.strip()
    except FileNotFoundError:
        return "nvidia-smi 不在 PATH（Apple Silicon 屬正常）"


def main() -> None:
    print(f"模型 {MODEL} / 取樣 temperature=0, top_p=1, seed=20260918")
    print(f"GPU 載入前：{nvidia()}")
    print()

    print("=== 一、延遲 ===")
    header = f"{'情境':<34}{'prompt':>8}{'TTFT':>9}{'生成':>7}{'速度':>11}"
    print(header)
    print("-" * len(header))
    scenarios = [
        ("冷啟動（短 prompt）", 0, "對方叫我去超商買點數然後拍序號給他", 80),
        ("S13 長 prompt（12 則示範題）首次", 12, "他說我的包裹卡在海關要我付關稅", 80),
        ("S13 長 prompt，前綴已快取", 12, "請詳細說明假檢警的完整話術流程", 500),
    ]
    for label, nex, q, npred in scenarios:
        r = generate(build_prompt(nex, q), npred)
        rate = r["gen_tok"] / (r["gen_ms"] / 1000) if r["gen_ms"] else 0
        print(
            f"{label:<34}{r['prompt_tok']:>6}t{r['ttft']:>7.0f}ms{r['gen_tok']:>5}t{rate:>8.1f}t/s"
        )
    size, proc = ollama_ps()
    print(f"\n模型佔用 {size} / PROCESSOR {proc} / {nvidia()}")
    if "100%" not in proc:
        print("[X] 有 CPU 分流！VRAM 不足，模型沒有完整跑在 GPU 上。")
    else:
        print("[OK] 100% 跑在 GPU。")

    print("\n=== 二、上下文天花板 ===")
    print("Ollama 溢出時不報錯，只安靜掉速。找出分界點：")
    print()
    header = f"{'num_ctx':>9}   {'模型佔用':>9}   {'PROCESSOR':<18}{'生成速度':>10}"
    print(header)
    print("-" * len(header))
    for ctx in (2048, 4096, 8192, 16384, 32768):
        subprocess.run(["ollama", "stop", MODEL], capture_output=True)
        time.sleep(2)
        try:
            r = generate("用一句話說明假檢警詐騙。", 16, num_ctx=ctx)
        except Exception as e:  # noqa: BLE001
            print(f"{ctx:>9}   失敗：{type(e).__name__}: {str(e)[:50]}")
            continue
        rate = r["gen_tok"] / (r["gen_ms"] / 1000) if r["gen_ms"] else 0
        size, proc = ollama_ps()
        flag = "" if "100%" in proc else "  <- 溢出"
        print(f"{ctx:>9}   {size:>9}   {proc:<18}{rate:>7.1f}t/s{flag}")


if __name__ == "__main__":
    main()
