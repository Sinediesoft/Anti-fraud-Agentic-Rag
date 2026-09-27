"""S13 驗收：地端小模型的階段判讀，跟規則層在同一批 60 筆上比。

對照組是 `threads_stages.assess`（規則層），不是 `keyword_score` ——
說明書要的是「跟最笨的做法比」，而階段判定這件事最笨的做法就是關鍵字規則，
它已經實作了，拿它當 baseline 比另外寫一個弱對照誠實。

門檻：相對提升 ≥ 10%（`shared.eval.relative_gain`）。

模型輸出會快取到 `data/slm_stage_cache.json`，prompt 變了就自動失效
（快取的鍵含 prompt 的 hash）。重跑不想用快取就加 --fresh。

用法：
    uv run python packages/modules/e_tbd/eval_stage.py
    uv run python packages/modules/e_tbd/eval_stage.py --fresh
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from packages.modules.e_tbd import (  # noqa: E402
    m4_judgement,
    slm_stage,
)
from packages.modules.e_tbd import threads_stages as ts  # noqa: E402
from packages.modules.e_tbd.m4_judgement import THREADS_METHOD  # noqa: E402
from packages.shared import eval as shared_eval  # noqa: E402
from packages.shared import models  # noqa: E402

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

GOLD = HERE / "eval" / "gold_rater1_v4.json"
SAMPLE = HERE / "data" / "gold_sample.jsonl"
CACHE = HERE / "data" / "slm_stage_cache.json"
OUT = HERE / "eval" / "stage_slm_v1.json"

ATTEMPTS = 2  # 跟 m4_judgement.judge 的重試次數一致，否則量到的不是同一條流程
NAME = slm_stage.STAGE_NAMES


def _key(text: str) -> str:
    return hashlib.sha256(slm_stage.build_prompt(text).encode("utf-8")).hexdigest()[:16]


def _run_slm(rows: list[dict], *, fresh: bool) -> tuple[dict[str, dict], list[float]]:
    cache: dict[str, dict] = {}
    if CACHE.exists() and not fresh:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))

    out: dict[str, dict] = {}
    latencies: list[float] = []
    for n, row in enumerate(rows, 1):
        key = _key(row["text"])
        hit = cache.get(key)
        if hit:
            out[row["case_id"]] = hit
            continue

        record = {"stage": "", "reason": "", "attempts": 0, "error": ""}
        started = time.perf_counter()
        for attempt in range(ATTEMPTS):
            record["attempts"] = attempt + 1
            try:
                call = slm_stage.classify(row["text"])
            except models.ModelNotSelectedError as exc:
                record["error"] = f"模型未鎖定：{exc}"
                break
            except Exception as exc:  # noqa: BLE001 —— 連不上也要記下來，不能中斷整批
                record["error"] = f"{type(exc).__name__}: {exc}"
                continue
            if call:
                record["stage"] = call.stage
                record["reason"] = call.reason
                # 三個事實也留著 —— 判錯時要看得出是哪一題抽錯，不是只知道選錯階段
                record["stolen"] = call.stolen
                record["handed"] = call.handed
                record["paid_times"] = call.paid_times
                record["error"] = ""
                break
            record["error"] = "輸出不合格式"
        latencies.append((time.perf_counter() - started) * 1000)

        out[row["case_id"]] = record
        cache[key] = record
        print(f"  {n:>2}/{len(rows)}  {record['stage'] or '（退回規則）':　<16}", flush=True)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return out, latencies


def _as_call(record: dict) -> slm_stage.StageCall | None:
    """把快取裡那一筆還原成 StageCall，才能餵給 m4_judgement.combine_stage。"""
    if not record.get("stage"):
        return None
    return slm_stage.StageCall(
        stage=record["stage"],
        reason=record.get("reason", ""),
        stolen=bool(record.get("stolen")),
        handed=bool(record.get("handed")),
        paid_times=int(record.get("paid_times", 0)),
        raw="",
    )


def _report(title: str, y_true: list[str], y_pred: list[str]) -> dict:
    rep = shared_eval.classification_report(y_true, y_pred)
    kappa = shared_eval.cohen_kappa(y_true, y_pred)
    print(f"\n{title}")
    print(f"  準確率 {rep.accuracy:.3f}　macro-F1 {rep.macro_f1:.3f}　kappa {kappa:.3f}")
    print(f"  {'階段':<20}{'precision':>10}{'recall':>8}{'f1':>7}{'支持數':>7}")
    for stage in slm_stage.STAGE_IDS:
        m = rep.per_label.get(stage)
        if not m:
            continue
        print(
            f"  {NAME[stage]:<16}{m['precision']:>10.2f}{m['recall']:>8.2f}"
            f"{m['f1']:>7.2f}{int(m['support']):>7}"
        )
    return {"accuracy": rep.accuracy, "macro_f1": rep.macro_f1, "kappa": kappa, "report": rep}


def _confusion(y_true: list[str], y_pred: list[str]) -> None:
    print(f"\n  {'標準答案＼判成':<18}" + "".join(f"{NAME[s][:4]:>6}" for s in slm_stage.STAGE_IDS))
    for t in slm_stage.STAGE_IDS:
        cells = "".join(
            f"{sum(1 for a, b in zip(y_true, y_pred, strict=True) if a == t and b == p):>6}"
            for p in slm_stage.STAGE_IDS
        )
        print(f"  {NAME[t]:<14}{cells}")


def main() -> int:
    fresh = "--fresh" in sys.argv
    if not GOLD.exists() or not SAMPLE.exists():
        print(f"缺檔案：{GOLD if not GOLD.exists() else SAMPLE}")
        return 1

    gold = {x["case_id"]: x["stage"] for x in json.loads(GOLD.read_text("utf-8"))["labels"]}
    rows = [json.loads(x) for x in SAMPLE.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r["case_id"] in gold]
    print(f"標準答案 {len(gold)} 筆，對得上原文的 {len(rows)} 筆\n")

    y_true = [gold[r["case_id"]] for r in rows]
    y_rule = [ts.assess(r["text"], THREADS_METHOD).harm.name.lower() for r in rows]

    print("呼叫模型中（有快取的直接跳過）…")
    slm, latencies = _run_slm(rows, fresh=fresh)

    # 退路：模型沒給答案就用規則層的，這跟 m4_judgement 的第三層一致
    y_slm = [slm[r["case_id"]]["stage"] or rule for r, rule in zip(rows, y_rule, strict=True)]
    fell_back = sum(1 for r in rows if not slm[r["case_id"]]["stage"])

    # 實際上線的那一條：決定性訊號歸規則，數次數歸模型（m4_judgement.combine_stage）
    calls = [_as_call(slm[r["case_id"]]) for r in rows]
    y_comb = [
        m4_judgement.combine_stage(rule, call) for rule, call in zip(y_rule, calls, strict=True)
    ]

    rule = _report("規則層（對照組）", y_true, y_rule)
    _confusion(y_true, y_rule)
    model = _report("純模型判階段（參考）", y_true, y_slm)
    comb = _report("規則＋模型（實際流程）", y_true, y_comb)
    _confusion(y_true, y_comb)

    gain_acc = shared_eval.relative_gain(rule["accuracy"], comb["accuracy"])
    gain_f1 = shared_eval.relative_gain(rule["macro_f1"], comb["macro_f1"])
    print(
        f"\n純模型 {model['accuracy']:.3f}　規則 {rule['accuracy']:.3f}　"
        f"合成 {comb['accuracy']:.3f}"
    )
    print(f"\n相對提升　準確率 {gain_acc:+.1%}　macro-F1 {gain_f1:+.1%}　（門檻 +10%）")
    print(f"退到規則層 {fell_back} / {len(rows)} 筆")
    if latencies:
        print(
            f"新呼叫的延遲 p95 {shared_eval.latency_p95(latencies):.0f} ms（{len(latencies)} 次）"
        )
    print("結論：" + ("達標" if gain_acc >= 0.10 else "未達標"))

    OUT.write_text(
        json.dumps(
            {
                "gold": "gold_rater1_v4",
                "n": len(rows),
                "baseline": {k: rule[k] for k in ("accuracy", "macro_f1", "kappa")},
                "slm_only": {k: model[k] for k in ("accuracy", "macro_f1", "kappa")},
                "combined": {k: comb[k] for k in ("accuracy", "macro_f1", "kappa")},
                "relative_gain_accuracy": gain_acc,
                "relative_gain_macro_f1": gain_f1,
                "fell_back": fell_back,
                "model": models.MODEL_LOCK["slm"].name,
                # 只存判定，不存原文 —— eval/ 會進版控
                "predictions": {
                    r["case_id"]: {"gold": gold[r["case_id"]], "rule": rr, "slm": ss, "final": cc}
                    for r, rr, ss, cc in zip(rows, y_rule, y_slm, y_comb, strict=True)
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n寫入 {OUT.relative_to(HERE.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
