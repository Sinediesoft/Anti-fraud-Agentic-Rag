"""S10：抽 60 筆標準答案樣本，並產生本機標註頁。

說明書 S10 要 60 筆人工標註的標準答案，兩人各標一遍算 Cohen's kappa（門檻 ≥ 0.70），
之後 S12 的考題與 `eval/route_questions.yaml` 的 `gold_case_ids` 都從這 60 筆來。

**標註對象是「受害程度」**（playbook 的五個 stage id）——
那是 `m4_judgement.detect_stage()` 的輸出，也是行動清單的索引鍵，
判錯就會給錯建議，所以標準答案要標它。

## 抽樣策略：每階段均勻 12 筆，不按母體比例

規則判定的母體分布是 none 7.5%／paid 14.9%／repeated 25.8%／
credentials 27.8%／drained 23.9%。按比例抽會讓 none 只有 4~5 筆，
而 S10 的目的**正是要檢驗規則判得對不對** —— 樣本少的階段一旦判錯，
用比例抽樣根本測不出來。所以每階段固定 12 筆。

階段內再按判定（SCAM／LIKELY／UNKNOWN）分層。`credentials` 與 `drained`
的母體 100% 是 SCAM（觸發條件本身就是決定性訊號），那兩層就全取 SCAM。

## 隱私

輸出的 HTML 內嵌案例原文，**只能留在本機**：不要上傳、不要轉傳、不要進版控。
含原文的樣本與標註頁輸出到 `data/`（已在 .gitignore）；
`eval/` 只放標註結果與統計，那些不含原文，可以進版控。

執行：
    uv run python packages/modules/e_tbd/make_gold_sample.py
"""

from __future__ import annotations

import io
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from packages.modules.e_tbd import threads_stages as ts  # noqa: E402
from packages.modules.e_tbd.m4_judgement import THREADS_METHOD  # noqa: E402

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

CASES = HERE / "data/cases.jsonl"
# 這兩個檔含案例原文，放 data/ 而不是 eval/ —— data/ 已在 .gitignore，
# eval/ 沒有。放錯地方一次 git add -A 就會把受害者陳述推進公開 repo。
OUT_SAMPLE = HERE / "data/gold_sample.jsonl"
OUT_PAGE = HERE / "data/gold_標註.html"

SEED = 20260922
PER_STAGE = 12
STAGES = ["none", "paid", "repeated", "credentials", "drained"]
STAGE_NAMES = {
    "none": "尚未付款",
    "paid": "已付款一次",
    "repeated": "已重複付款",
    "credentials": "已交付帳戶控制權",
    "drained": "帳戶遭盜用",
}
VERDICTS = ["SCAM", "LIKELY", "UNKNOWN"]

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>S10 標準答案標註（60 筆）</title>
<style>
:root{--bg:#fbfaf8;--fg:#24211d;--mut:#6b655c;--line:#e0dbd2;--card:#fff;--ok:#1f7a4d;--bad:#b23b3b}
@media(prefers-color-scheme:dark){:root{--bg:#191817;--fg:#eae6df;--mut:#9a938a;
--line:#34312c;--card:#211f1d;--ok:#4caf7d;--bad:#e07a7a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 system-ui,"Noto Sans TC",sans-serif}
.wrap{max-width:800px;margin:0 auto;padding:22px 16px 130px}
h1{font-size:19px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:16px}
.prev{margin-top:12px;padding:8px 11px;border-radius:7px;background:var(--bg);border:1px dashed var(--line);font-size:13px;color:var(--mut)}
.rule{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:13px 15px;font-size:13.5px;line-height:1.75;margin-bottom:14px}
.rule u{text-decoration:underline;text-underline-offset:2px}
.warn{border:1px solid var(--bad);border-left-width:3px;border-radius:6px;padding:10px 13px;
color:var(--mut);font-size:13px;margin-bottom:16px;background:var(--card)}
.bar{height:5px;background:var(--line);border-radius:3px;overflow:hidden;margin-bottom:18px}
.bar>i{display:block;height:100%;background:var(--ok);transition:width .2s}
.meta{display:flex;gap:10px;flex-wrap:wrap;color:var(--mut);font-size:12.5px;margin-bottom:8px}
.tag{background:var(--line);padding:1px 8px;border-radius:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px}
.text{white-space:pre-wrap;word-break:break-word;max-height:46vh;overflow:auto}
.opts{display:flex;flex-direction:column;gap:7px;margin-top:16px}
button{text-align:left;padding:10px 13px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--fg);font:inherit;font-size:14px;cursor:pointer}
button:hover{border-color:var(--fg)}
button kbd{background:var(--line);border-radius:4px;padding:1px 6px;font:12px monospace;margin-right:8px}
button small{color:var(--mut);margin-left:6px}
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
<h1>S10 標準答案標註</h1>
<div class=sub><b>判準（第三版，2026-09-22）</b>：第二版說「取最嚴重」，但那預設五級在同一條
嚴重度階梯上——其實「匯了幾筆錢」和「帳戶有沒有失守」是兩條獨立的線。
第三版改成依<u>行動緊急程度</u>排序，並把最容易混淆的那組講明白。</div>
<div class=rule>
<b>這五級是依「<u>行動的緊急程度</u>」排的，不是依損失金額。</b>
帳戶還在對方可動用的狀態最緊急（要立刻停卡），自己匯出去的錢再多，
能做的也只有報案追償。所以下面的順序不是「損失由小到大」。
<br><br>
<b>判斷順序：先看帳戶，再看付款。</b><br>
① 帳戶被別人動用了嗎（盜刷、盜轉、不明交易）→ <b>帳戶遭盜用</b><br>
② 交出了驗證碼／證件／提款卡，或在對方指示下操作 ATM、做網銀驗證
→ <b>已交付帳戶控制權</b><br>
③ 帳戶沒事，看自己匯出幾筆 → 兩筆以上<b>已重複付款</b>／一筆<b>已付款</b>／沒匯<b>尚未付款</b>
<br><br>
<b>最容易混淆的一組：</b><br>
・「被騙走 5 萬元」——<u>自己匯出去的</u> → <b>已付款</b>或<b>已重複付款</b><br>
・「帳戶被盜刷 5 萬元」——<u>別人動的</u> → <b>帳戶遭盜用</b><br>
兩者金額一樣、都很慘，但前者要報案追償，後者要立刻停卡阻止繼續被提。
<br><br>
<b>只算已經發生的，不算對方要求或話術。</b><br>
・「對方叫我匯款，我覺得不對沒匯」→ <b>尚未付款</b><br>
・「他說只要掃碼扣款就能完成交易」→ 那是話術，錢還沒出去<br>
・用網銀或 ATM <u>正常轉帳付款</u>不算交出控制權，那只是付款方式
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
  const opts = STAGES.map((s, n) =>
    `<button onclick="mark('${s.id}')"><kbd>${n+1}</kbd>${esc(s.name)}<small>${esc(s.hint)}</small></button>`
  ).join("");
  const prevName = r.prev ? (STAGES.find(s => s.id === r.prev) || {}).name : "";
  const prevTag = prevName
    ? `<div class=prev>第二輪你標：<b>${esc(prevName)}</b>　—　新判準下若不變，直接按同一個</div>` : "";
  return `<div class=meta><span class=tag>${esc(r.label)}</span><span>${esc(r.county)}</span>` +
    `<span>${esc(r.date)}</span><span>第 ${i+1} / ${DATA.length} 筆</span></div>` +
    `<div class=card><div class=text>${esc(r.text)}</div>${prevTag}<div class=opts>${opts}</div>` +
    `<div class=nav><button onclick="back()">上一筆</button>` +
    `<span>已標 ${Object.keys(marks).length} 筆，可隨時關閉，進度會留著</span></div></div>`;
}
function summary(){
  const c = {}; STAGES.forEach(s => c[s.id] = 0);
  let agree = 0;
  DATA.forEach(r => { const m = marks[r.case_id]; if (m){ c[m]++; if (m === r.rule) agree++; } });
  const rows = STAGES.map(s =>
    `<tr><td>${s.name}</td><td class=n>${c[s.id]}</td></tr>`).join("");
  const out = JSON.stringify({
    rater: "__RATER__",
    labels: DATA.map(r => ({case_id: r.case_id, stage: marks[r.case_id] || null})),
  }, null, 2);
  return `<div class=done><h1>標完了</h1>
    <table><tr><th>階段</th><th class=n>筆數</th></tr>${rows}</table>
    <p>與規則判定一致：<b>${agree} / ${DATA.length}</b>
      （${(agree/DATA.length*100).toFixed(1)}%）—— 這不是準確率，
      是「人工標註和現行規則的重合度」，差距大的地方正是要改規則的地方。</p>
    <p style="color:var(--mut);font-size:13px">把下面這段存成
      <code>eval/gold_rater1_v3.json</code>，交給 Claude 算 kappa：</p>
    <pre>${esc(out)}</pre>
    <div class=nav><button onclick="i=0;render()">重看</button>
      <button onclick="if(confirm('清空重標？')){marks={};save();i=0;render()}">清空</button></div>
  </div>`;
}
addEventListener("keydown", e => {
  if (i >= DATA.length) return;
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= STAGES.length) mark(STAGES[n-1].id);
  else if (e.key === "Backspace"){ e.preventDefault(); back(); }
});
render();
</script>
"""

HINTS = {
    "none": "還沒給出金錢或個資",
    "paid": "付過一次錢",
    "repeated": "付了兩次以上",
    "credentials": "給了驗證碼／證件／卡片，或操作過 ATM、網銀",
    "drained": "帳戶被別人動用（盜刷、盜轉）",
}


def main() -> int:
    if not CASES.exists():
        print(f"找不到語料子集：{CASES}\n請先跑 m1_corpus.py")
        return 1

    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]
    print(f"母體 {len(cases):,} 筆")

    # 樣本已存在就沿用同一批 case_id —— 重抽會讓新舊兩輪標註無法比較，
    # 而判準改寫後本來就要在「同一批案例」上重標才看得出差異。
    if OUT_SAMPLE.exists():
        keep = [json.loads(x)["case_id"] for x in OUT_SAMPLE.read_text(encoding="utf-8").splitlines() if x]
        by_id = {c["case_id"]: c for c in cases}
        picked = [by_id[i] for i in keep if i in by_id]
        if len(picked) == len(keep):
            print(f"沿用既有樣本 {len(picked)} 筆（規則判定會用目前的版本重算）")
            for c in picked:
                a = ts.assess(c["text"], THREADS_METHOD)
                c["_rule_stage"] = a.harm.name.lower()
                c["_rule_verdict"] = a.verdict.name
            return _write(picked)
        print("既有樣本對不上目前語料，重新抽樣")

    # 依規則判定分層
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in cases:
        a = ts.assess(c["text"], THREADS_METHOD)
        c["_rule_stage"] = a.harm.name.lower()
        c["_rule_verdict"] = a.verdict.name
        buckets[(c["_rule_stage"], a.verdict.name)].append(c)

    rng = random.Random(SEED)
    picked: list[dict] = []
    print(f"\n每階段 {PER_STAGE} 筆，階段內按判定分層：")
    for stage in STAGES:
        avail = {v: buckets[(stage, v)] for v in VERDICTS if buckets[(stage, v)]}
        # 各判定層先均分，除不盡的名額給母體最大的層
        base, extra = divmod(PER_STAGE, len(avail))
        quota = {v: base for v in avail}
        for v in sorted(avail, key=lambda v: -len(avail[v]))[:extra]:
            quota[v] += 1

        got = []
        for v, q in quota.items():
            pool = avail[v]
            got += rng.sample(pool, min(q, len(pool)))
        # 某層母體不足時，從同階段其他層補滿
        if len(got) < PER_STAGE:
            rest = [c for v in avail for c in avail[v] if c not in got]
            got += rng.sample(rest, min(PER_STAGE - len(got), len(rest)))
        picked += got
        detail = "　".join(f"{v} {len([c for c in got if c['_rule_verdict']==v])}" for v in avail)
        print(f"  {stage:<12}{len(got):>3} 筆　（{detail}）")

    rng.shuffle(picked)  # 打散，避免標註時看出分層順序
    return _write(picked)


def _write(picked: list[dict]) -> int:
    # `prev` 永遠留空 —— 不把上一輪的答案帶進頁面。
    #
    # v3 那次帶了 v2 的答案當參考（理由是判準只改一組、全部重標不划算），
    # 結果 v2 vs v3 的 kappa 高達 0.906，卻分不出是「判準修好所以穩定」
    # 還是「看到參考答案就按同一個」。拿掉參考重判 14 筆，13 筆改變 ——
    # 那個 0.906 主要是 anchoring。
    #
    # 2026-09-24 這一輪更沒有帶的理由：與模組 D 談定界線後語料重切
    # （9,167 → 4,140），舊的 60 筆有 31 筆已不在本模組範圍，樣本是全新抽的。
    prev: dict[str, str] = {}
    rows = [
        {
            "case_id": c["case_id"],
            "text": c["text"],
            "label": c.get("label", ""),
            "date": (c.get("date") or "")[:10],
            "county": c.get("county", ""),
            "rule": c["_rule_stage"],
            "rule_verdict": c["_rule_verdict"],
            "prev": prev.get(c["case_id"], ""),
        }
        for c in picked
    ]

    OUT_SAMPLE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_SAMPLE.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    stages_js = [{"id": s, "name": STAGE_NAMES[s], "hint": HINTS[s]} for s in STAGES]
    page = (
        PAGE.replace("__DATA__", json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__STAGES__", json.dumps(stages_js, ensure_ascii=False))
        # key 每一輪都要換：瀏覽器的 localStorage 還留著上一輪的標註，
        # 沿用同一個 key 會讀到對不上的舊 marks
        .replace("__KEY__", "gold_s10_v4_rater1")
        .replace("__RATER__", "rater1_v4")
    )
    OUT_PAGE.write_text(page, encoding="utf-8")

    print(f"\n樣本   {OUT_SAMPLE.relative_to(HERE.parent.parent.parent)}（{len(rows)} 筆）")
    print(f"標註頁 {OUT_PAGE.relative_to(HERE.parent.parent.parent)}")
    print("\n第二位標註者：同一份 HTML，在另一台瀏覽器或無痕視窗標即可")
    print("（localStorage 是 gold_s10_v4_rater1，不同瀏覽器互不干擾）。")
    print("兩份結果存成 eval/gold_rater1_v4.json、gold_rater2_v4.json 後算 kappa。")
    print("[!] 這份含真實案例原文，要交給組員請走實體或團隊共用硬碟。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
