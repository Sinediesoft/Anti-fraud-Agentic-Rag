"""判準仲裁頁：兩位標註者不一致的那 25 筆，由人裁決（S10 收尾）。

kappa 0.450 沒過 0.70，但**不是因為標得亂**。25 筆不一致裡，第二位標註者
判得比較嚴重的有 22 筆、比較輕的只有 3 筆 —— 那是系統性偏差不是雜訊。
集中在兩種：

    rater1 已交付控制權 ↔ rater2 帳戶遭盜用    7 筆
    rater1 已付款      ↔ rater2 已交付控制權   7 筆

兩種都踩在判準的優先序上（①帳戶 → ②交出 → ③次數）。判準寫了順序，
**但沒寫「訊號要多強才算數」** —— 一位標註者要那件事是案子的主軸才往上跳，
另一位只要文中出現就往上跳。兩種讀法都符合字面。

## 所以這一輪不是重標，是仲裁

一致的 35 筆直接沿用，只裁決不一致的 25 筆。這是標註工作的標準做法
（annotator disagreement → adjudication），而且 kappa 要用**仲裁前**的兩份
標註算，不能用仲裁後的 —— 仲裁後的一致度必然是 1.0，那個數字沒有意義。

## 頁面會給裁決者看兩邊的答案

跟 rater2 的標註頁刻意相反。那一頁的任務是「獨立標註」，看到別人的答案
就毀了；這一頁的任務是「在兩個已知答案之間做決定」，不給反而沒得判。

另外給一條判準裡沒寫、這次要補進去的操作型問題：

    錢出去的那一筆，是不是受害者自己按下確認的？

    自己按的（即使是被指示的）→ 已付款／已重複付款
    不是自己按的（對方遠端操作、盜刷、事後才發現）→ 帳戶遭盜用
    工具交出去了但錢還沒動 → 已交付帳戶控制權

執行：
    uv run python packages/modules/e_tbd/make_adjudication_page.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from packages.modules.e_tbd.make_gold_sample import STAGE_NAMES, STAGES  # noqa: E402

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

CASES = HERE / "data/cases.jsonl"
RATER1 = HERE / "eval/gold_rater1_v4.json"
RATER2 = HERE / "eval/rater_claude_v4.json"
OUT_PAGE = HERE / "data/判準仲裁.html"

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>判準仲裁（__N__ 筆）</title>
<style>
:root{--bg:#fbfaf8;--fg:#24211d;--mut:#6b655c;--line:#e0dbd2;--card:#fff;--ok:#1f7a4d;--bad:#b23b3b;--pick:#2f6f9f}
@media(prefers-color-scheme:dark){:root{--bg:#191817;--fg:#eae6df;--mut:#9a938a;
--line:#34312c;--card:#211f1d;--ok:#4caf7d;--bad:#e07a7a;--pick:#7fb3d8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 system-ui,"Noto Sans TC",sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:22px 16px 130px}
h1{font-size:19px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:14px}
.rule{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:13px 15px;
font-size:13.5px;line-height:1.75;margin-bottom:14px}
.rule b{color:var(--pick)}
.warn{border:1px solid var(--bad);border-left-width:3px;border-radius:6px;padding:10px 13px;
color:var(--mut);font-size:13px;margin-bottom:16px;background:var(--card)}
.bar{height:5px;background:var(--line);border-radius:3px;overflow:hidden;margin-bottom:18px}
.bar>i{display:block;height:100%;background:var(--ok);transition:width .2s}
.meta{display:flex;gap:10px;flex-wrap:wrap;color:var(--mut);font-size:12.5px;margin-bottom:8px}
.tag{background:var(--line);padding:1px 8px;border-radius:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px}
.text{white-space:pre-wrap;word-break:break-word;max-height:44vh;overflow:auto}
.two{display:flex;gap:8px;margin-top:16px}
.two button{flex:1}
.opts{display:flex;flex-direction:column;gap:7px;margin-top:8px}
button{text-align:left;padding:10px 13px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--fg);font:inherit;font-size:14px;cursor:pointer}
button:hover{border-color:var(--fg)}
button.main{border-color:var(--pick);border-width:2px}
button kbd{background:var(--line);border-radius:4px;padding:1px 6px;font:12px monospace;margin-right:8px}
button small{color:var(--mut);display:block;margin-top:2px}
.nav{display:flex;gap:8px;align-items:center;margin-top:14px;color:var(--mut);font-size:13px}
.nav button{flex:0 0 auto;padding:6px 13px}
.done{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:22px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:6px 9px;text-align:left}
td.n{text-align:right;font-variant-numeric:tabular-nums}
pre{background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:12px;
overflow:auto;font-size:12px;max-height:280px}
</style>
<div class=wrap>
<h1>判準仲裁</h1>
<div class=sub>兩位標註者一致的 __AGREE__ 筆直接沿用，這裡只裁決不一致的
<b>__N__ 筆</b>。兩邊的答案都會給你看 —— 這一頁的任務是在兩個已知答案之間做決定，
不是重新獨立標註。</div>
<div class=rule>
<b>判準少寫了一條，這次補上：錢出去的那一筆，是不是你自己按下確認的？</b><br>
・<b>自己按的</b>（即使是照對方指示按的）→ 已付款一次／已重複付款<br>
・<b>不是自己按的</b>（對方遠端操作、盜刷、事後才發現餘額少了）→ 帳戶遭盜用<br>
・<b>工具交出去了但錢還沒動</b>（驗證碼、證件、提款卡、取款碼）→ 已交付帳戶控制權
<br><br>
__N__ 筆不一致裡有 <b>__CASCADE__ 筆</b>卡在這條線上。原本的判準只寫了「先看帳戶，再看付款」，
<u>沒寫訊號要多強才算數</u>，所以一位標註者要那件事是案子的主軸才往上跳，
另一位只要文中出現就往上跳 —— 兩種讀法都符合字面。
</div>
<div class=warn>含真實受害者陳述原文。只能留在本機 — 不要上傳、不要轉傳、不要進版控。</div>
<div class=bar><i id=bar></i></div>
<div id=app></div>
</div>
<script>
const DATA = __DATA__, STAGES = __STAGES__, KEY = "__KEY__";
let marks = {}; try{ marks = JSON.parse(localStorage.getItem(KEY)) || {} }catch(e){}
let i = DATA.findIndex(r => !(r.case_id in marks)); if (i < 0) i = DATA.length;
const esc = s => s.replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const stageName = id => (STAGES.find(s => s.id === id) || {}).name || id;
const save = () => { try{ localStorage.setItem(KEY, JSON.stringify(marks)) }catch(e){} };
function mark(v){ marks[DATA[i].case_id] = v; save(); i++; render(); }
function back(){ if (i > 0){ i--; delete marks[DATA[i].case_id]; save(); render(); } }

function render(){
  document.getElementById("bar").style.width =
    (Object.keys(marks).length / DATA.length * 100) + "%";
  document.getElementById("app").innerHTML = i >= DATA.length ? summary() : card();
}
function card(){
  const r = DATA[i];
  const others = STAGES.filter(s => s.id !== r.a && s.id !== r.b);
  const two =
    `<button class=main onclick="mark('${r.a}')"><kbd>1</kbd>${esc(stageName(r.a))}` +
    `<small>第一位標註者</small></button>` +
    `<button class=main onclick="mark('${r.b}')"><kbd>2</kbd>${esc(stageName(r.b))}` +
    `<small>第二位標註者</small></button>`;
  const rest = others.map((s, n) =>
    `<button onclick="mark('${s.id}')"><kbd>${n+3}</kbd>${esc(s.name)}<small>兩邊都不對</small></button>`
  ).join("");
  return `<div class=meta><span class=tag>${esc(r.label)}</span><span>${esc(r.county)}</span>` +
    `<span>${esc(r.date)}</span><span>第 ${i+1} / ${DATA.length} 筆</span></div>` +
    `<div class=card><div class=text>${esc(r.text)}</div>` +
    `<div class=two>${two}</div><div class=opts>${rest}</div>` +
    `<div class=nav><button onclick="back()">上一筆</button>` +
    `<span>已裁決 ${Object.keys(marks).length} 筆，可隨時關閉，進度會留著</span></div></div>`;
}
function summary(){
  let toA = 0, toB = 0, toC = 0;
  DATA.forEach(r => { const m = marks[r.case_id];
    if (m === r.a) toA++; else if (m === r.b) toB++; else if (m) toC++; });
  const full = AGREED.concat(DATA.map(r => ({case_id: r.case_id, stage: marks[r.case_id] || null})));
  const out = JSON.stringify({
    rater: "adjudicated_v5",
    note: "__NOTE__",
    labels: full,
  }, null, 2);
  return `<div class=done><h1>裁決完了</h1>
    <table><tr><th>裁決結果</th><th class=n>筆數</th></tr>
      <tr><td>採用第一位標註者</td><td class=n>${toA}</td></tr>
      <tr><td>採用第二位標註者</td><td class=n>${toB}</td></tr>
      <tr><td>兩邊都不採用</td><td class=n>${toC}</td></tr></table>
    <p style="color:var(--mut);font-size:13px">下面這段是<b>完整 60 筆</b>
      （一致的 ${AGREED.length} 筆 ＋ 裁決的 ${DATA.length} 筆）。
      存成 <code>eval/gold_adjudicated_v5.json</code>。<br>
      <b>kappa 仍然要用仲裁前的兩份算</b> —— 仲裁後的一致度必然是 1.0，沒有意義。</p>
    <pre>${esc(out)}</pre>
    <div class=nav><button onclick="i=0;render()">重看</button>
      <button onclick="if(confirm('清空重裁？')){marks={};save();i=0;render()}">清空</button></div>
  </div>`;
}
const AGREED = __AGREED__;
addEventListener("keydown", e => {
  if (i >= DATA.length) return;
  const r = DATA[i], order = [r.a, r.b, ...STAGES.filter(s => s.id !== r.a && s.id !== r.b).map(s => s.id)];
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= order.length) mark(order[n-1]);
  else if (e.key === "Backspace"){ e.preventDefault(); back(); }
});
render();
</script>
"""


def main() -> int:
    for path in (CASES, RATER1, RATER2):
        if not path.exists():
            print(f"缺檔案：{path}")
            return 1

    r1 = {x["case_id"]: x["stage"] for x in json.loads(RATER1.read_text("utf-8"))["labels"]}
    r2 = {x["case_id"]: x["stage"] for x in json.loads(RATER2.read_text("utf-8"))["labels"]}
    by_id = {
        c["case_id"]: c
        for c in (
            json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line
        )
    }

    shared = [c for c in r1 if c in r2]
    agreed = [{"case_id": c, "stage": r1[c]} for c in shared if r1[c] == r2[c]]
    disputed = [c for c in shared if r1[c] != r2[c]]
    missing = [c for c in disputed if c not in by_id]
    if missing:
        print(f"有 {len(missing)} 筆在語料裡找不到，先跑 m1_corpus.py")
        return 1

    rows = [
        {
            "case_id": c,
            "text": by_id[c]["text"],
            "label": by_id[c].get("label", ""),
            "date": (by_id[c].get("date") or "")[:10],
            "county": by_id[c].get("county", ""),
            "a": r1[c],
            "b": r2[c],
        }
        for c in disputed
    ]

    note = (
        f"2026-09-28 仲裁。一致的 {len(agreed)} 筆沿用，不一致的 {len(rows)} 筆由人裁決。"
        f"kappa 請用仲裁前的 gold_rater1_v4 與 rater_claude_v4 計算。"
    )
    # 「卡在優先序上」= 兩邊落在不同層級（③次數 / ②交出 / ①帳戶）
    level = {"none": 0, "paid": 0, "repeated": 0, "credentials": 1, "drained": 2}
    cascade = sum(1 for c in disputed if level[r1[c]] != level[r2[c]])

    stages_js = [{"id": s, "name": STAGE_NAMES[s]} for s in STAGES]
    page = (
        PAGE.replace("__DATA__", json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__AGREED__", json.dumps(agreed, ensure_ascii=False))
        .replace("__STAGES__", json.dumps(stages_js, ensure_ascii=False))
        .replace("__KEY__", "gold_v5_adjudication")
        .replace("__NOTE__", note)
        .replace("__AGREE__", str(len(agreed)))
        .replace("__CASCADE__", str(cascade))
        .replace("__N__", str(len(rows)))
    )
    OUT_PAGE.write_text(page, encoding="utf-8")

    print(f"兩份標註共有 {len(shared)} 筆：一致 {len(agreed)}、不一致 {len(rows)}")
    print(f"仲裁頁 {OUT_PAGE.relative_to(HERE.parent.parent.parent)}")
    print("裁決完把頁尾那段 JSON 存成 eval/gold_adjudicated_v5.json（含完整 60 筆）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
