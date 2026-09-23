"""產生檢索相關性判定頁（S12 Recall@5）。

**為什麼需要這一步**：`eval/route_questions.yaml` 每題只標了一筆 `gold_case_ids`
（那題是從哪一筆案例改寫的）。但語料有 9,167 筆、71.5% 是同一個 label，
講的都是賣貨便加實名認證 —— 一句查詢必然對應到好幾十筆同樣切題的案例。
拿「有沒有撈回改寫來源那一筆」當 Recall，會把「撈到另一筆一樣切題的案例」
算成失敗，量到的是低估值。

`shared.eval.recall_at_k` 的定義是「前 k 筆有沒有命中正確答案」，
所以只要判定每題 Top-5 這幾筆相不相關，就能算出沒有低估的 Recall@5，
不需要把 9,167 筆全部標完。這是 IR 的標準做法（pooling + relevance judgement）。

**判定池**＝現行檢索的 Top-5 ∪ 原本的 gold。把 gold 也放進去是必要的：
不放的話，原本算命中的題目會因為 gold 沒被判定過而變成失敗。

**頁面刻意不顯示哪一筆是 gold、也不顯示名次**——
S10 那次就是因為標註頁帶入上一輪答案，kappa 0.906 分不出「判準修好」和「照抄」。

輸出放 data/（含案例原文，已在 .gitignore）。

執行：
    uv run python packages/modules/e_tbd/make_retrieval_judgements.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from modules.e_tbd import m3_retrieval as M  # noqa: E402
from shared import deid  # noqa: E402

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS = HERE / "eval" / "route_questions.yaml"
OUT = HERE / "data" / "檢索判定.html"
TOP_K = 5
EXCERPT = 220

PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>檢索相關性判定</title>
<style>
:root{--bg:#fbfaf8;--fg:#24211d;--mut:#6b655c;--line:#e0dbd2;--card:#fff;--ok:#1f7a4d;--bad:#b23b3b}
@media(prefers-color-scheme:dark){:root{--bg:#191817;--fg:#eae6df;--mut:#9a938a;
--line:#34312c;--card:#211f1d;--ok:#4caf7d;--bad:#e07a7a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 system-ui,"Noto Sans TC",sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:22px 16px 150px}
h1{font-size:19px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:18px}
.q{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px 18px;margin-bottom:14px}
.q .label{color:var(--mut);font-size:12px;letter-spacing:.06em}
.q .text{font-size:17px;font-weight:600;margin-top:4px}
.case{background:var(--card);border:1px solid var(--line);border-left-width:4px;
border-radius:8px;padding:13px 16px;margin-bottom:9px}
.case.cur{border-left-color:#c8a04a;box-shadow:0 0 0 2px rgba(200,160,74,.18)}
.case.yes{border-left-color:var(--ok)}
.case.no{border-left-color:var(--bad);opacity:.55}
.meta{color:var(--mut);font-size:12px;margin-bottom:5px}
.excerpt{font-size:14px}
.btns{margin-top:9px;display:flex;gap:8px}
button{font:inherit;padding:5px 14px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--fg);cursor:pointer}
button.on-yes{background:var(--ok);color:#fff;border-color:var(--ok)}
button.on-no{background:var(--bad);color:#fff;border-color:var(--bad)}
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--card);
border-top:1px solid var(--line);padding:11px 16px}
.bar .inner{max-width:820px;margin:0 auto;display:flex;gap:14px;align-items:center;
justify-content:space-between;flex-wrap:wrap}
.prog{height:5px;background:var(--line);border-radius:3px;flex:1;min-width:160px}
.prog i{display:block;height:100%;background:var(--ok);border-radius:3px;width:0}
textarea{width:100%;height:150px;margin-top:10px;font:12px/1.5 ui-monospace,monospace;
background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:9px}
kbd{background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:1px 6px;font-size:12px}
.done{color:var(--mut);font-size:13px}
</style></head><body><div class="wrap">
<h1>檢索相關性判定</h1>
<p class="sub">每一題底下是系統撈回來的案例。問題只有一個：
<b>這筆案例跟這句話講的是不是同一種情況？</b>
是就按 <kbd>1</kbd>，不是按 <kbd>2</kbd>，<kbd>Backspace</kbd> 回上一筆。<br>
判「是」不需要每個細節都一樣 —— 只要受害者看到它會覺得「對，我遇到的就是這個」就算。
順序是打散的，不代表系統認為誰比較相關。</p>
<div id="app"></div>
<div class="bar"><div class="inner">
<div class="prog"><i id="pi"></i></div>
<span id="stat"></span>
<button id="dump">產生結果</button>
</div><textarea id="out" style="display:none" readonly></textarea></div>
</div>
<script>
const DATA = __DATA__;
const KEY = "retrieval_judge_v1";
let marks = {}; try{ marks = JSON.parse(localStorage.getItem(KEY)) || {} }catch(e){}
const flat = [];
DATA.forEach((q, qi) => q.cases.forEach((c, ci) => flat.push([qi, ci])));
let cur = flat.findIndex(([qi, ci]) => marks[DATA[qi].cases[ci].key] === undefined);
if (cur < 0) cur = flat.length - 1;

const save = () => { try{ localStorage.setItem(KEY, JSON.stringify(marks)) }catch(e){} };

function render(){
  const app = document.getElementById("app");
  app.innerHTML = DATA.map((q, qi) => `
    <div class="q"><div class="label">第 ${qi+1} 題</div><div class="text">${q.text}</div></div>
    ${q.cases.map((c, ci) => {
      const m = marks[c.key];
      const isCur = flat[cur] && flat[cur][0] === qi && flat[cur][1] === ci;
      return `<div class="case ${m === true ? "yes" : m === false ? "no" : ""} ${isCur ? "cur" : ""}"
                   id="c-${qi}-${ci}">
        <div class="meta">${c.meta}</div>
        <div class="excerpt">${c.excerpt}</div>
        <div class="btns">
          <button class="${m === true ? "on-yes" : ""}" onclick="mark(${qi},${ci},true)">是</button>
          <button class="${m === false ? "on-no" : ""}" onclick="mark(${qi},${ci},false)">不是</button>
        </div></div>`;
    }).join("")}`).join("");
  const done = Object.keys(marks).length;
  document.getElementById("pi").style.width = (done / flat.length * 100) + "%";
  document.getElementById("stat").textContent = `${done} / ${flat.length}`;
  const el = document.querySelector(".case.cur");
  if (el) el.scrollIntoView({block: "center", behavior: "smooth"});
}

function mark(qi, ci, val){
  marks[DATA[qi].cases[ci].key] = val;
  save();
  const at = flat.findIndex(([a, b]) => a === qi && b === ci);
  if (at >= 0 && at + 1 < flat.length) cur = at + 1;
  render();
}
function back(){ if (cur > 0){ cur--; render(); } }

addEventListener("keydown", e => {
  if (!flat[cur]) return;
  const [qi, ci] = flat[cur];
  if (e.key === "1"){ e.preventDefault(); mark(qi, ci, true); }
  else if (e.key === "2"){ e.preventDefault(); mark(qi, ci, false); }
  else if (e.key === "Backspace"){ e.preventDefault(); back(); }
});

document.getElementById("dump").onclick = () => {
  const missing = flat.filter(([qi, ci]) => marks[DATA[qi].cases[ci].key] === undefined).length;
  const out = JSON.stringify({
    rater: "rater1",
    judgements: marks,
  }, null, 1);
  const ta = document.getElementById("out");
  ta.style.display = "block";
  ta.value = (missing ? `// 還有 ${missing} 筆沒判\\n` : "") + out;
  ta.select();
};
render();
</script></body></html>
"""


def main() -> int:
    questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8")) or []
    pool = M.load_local()
    by_id = {c.case_id: c for c in pool}
    print(f"語料 {len(pool)} 筆，題目 {len(questions)} 題")

    data = []
    total = 0
    for qi, q in enumerate(questions):
        hits = M.search(q["text"], top_k=TOP_K, cases=pool)
        ids = [h.case_id for h in hits]
        # gold 一定要進判定池：不放的話，原本算命中的題目會因為 gold 沒被判定過
        # 而被當成失敗，等於用判定把分數往下壓
        for gold in q.get("gold_case_ids", []):
            if gold not in ids and gold in by_id:
                ids.append(gold)

        cases = []
        for cid in ids:
            case = by_id[cid]
            safe = deid.mask(case.text)
            meta = " · ".join(x for x in (case.date, case.county, case.label) if x)
            cases.append(
                {
                    "key": f"{qi}|{cid}",
                    "meta": meta or "（無日期縣市）",
                    "excerpt": safe.text[:EXCERPT] + ("…" if len(safe.text) > EXCERPT else ""),
                }
            )
        # 打散，避免名次本身變成暗示
        order = sorted(range(len(cases)), key=lambda i: (ids[i] + str(qi)))
        cases = [cases[i] for i in order]
        total += len(cases)
        data.append({"text": q["text"], "cases": cases})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    page = PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    OUT.write_text(page, encoding="utf-8")
    rel = OUT.relative_to(HERE.parent.parent.parent)
    print(f"判定頁 {rel}（{len(data)} 題、共 {total} 筆要判）")
    print("判完按「產生結果」，把 JSON 存成 eval/retrieval_judgements.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
