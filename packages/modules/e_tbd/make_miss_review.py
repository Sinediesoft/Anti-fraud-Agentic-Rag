"""把「規則判尚未付款、人工標了別的」那幾筆攤開來看（S10 診斷用）。

規則靠付款動詞判付款，語料裡 12.4% 沒有付款動詞，就整批落到「尚未付款」。
gold v4 顯示這類案例 12 筆裡有 10 筆人工標成別的階段。要修規則得先知道
人工是從哪些字判斷的 —— 這支腳本只負責把原文攤開，不做任何推論。

輸出放 data/（含案例原文，已在 .gitignore）。

用法：
    uv run python packages/modules/e_tbd/make_miss_review.py            # 全部 10 筆
    uv run python packages/modules/e_tbd/make_miss_review.py credentials # 只看某一階
"""

from __future__ import annotations

import html
import io
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared import deid  # noqa: E402

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

GOLD = HERE / "eval" / "gold_rater1_v4.json"
SAMPLE = HERE / "data" / "gold_sample.jsonl"
OUT = HERE / "data" / "規則漏判複查.html"

NAME = {
    "none": "尚未付款",
    "paid": "已付款",
    "repeated": "已重複付款",
    "credentials": "已交付帳戶控制權",
    "drained": "帳戶遭盜用",
}

# 想在原文裡標出來的線索。只是上色，不參與任何判定。
MARKS = [
    ("pay", r"匯款|匯了|匯出|匯過去|轉帳|轉了|轉出|轉過去|付款|付了|付錢|支付|繳款|繳了"),
    ("loss", r"(被騙|詐騙|損失|遭騙|受騙)[^。，]{0,12}?[0-9０-９][0-9０-９,，]*\s*(元|萬)"),
    ("lossword", r"被騙|受騙|遭騙|損失|詐騙金額"),
    ("cred", r"帳號|密碼|帳密|驗證碼|OTP|網銀|網路銀行|身分證|證件|存摺|金融卡|提款卡|認證"),
    ("money", r"[0-9０-９][0-9０-９,，]*\s*(元|萬)"),
]

PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>規則漏判複查</title>
<style>
:root{--bg:#fbfaf8;--fg:#24211d;--mut:#6b655c;--line:#e0dbd2;--card:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#191817;--fg:#eae6df;--mut:#9a938a;
--line:#34312c;--card:#211f1d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.85 system-ui,"Noto Sans TC",sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:19px;margin:0 0 6px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.case{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px 18px;margin-bottom:14px}
.hd{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;
margin-bottom:9px;padding-bottom:9px;border-bottom:1px solid var(--line)}
.n{font-weight:700;font-size:16px}
.tag{font-size:12px;padding:2px 9px;border-radius:99px;border:1px solid var(--line)}
.tag.rule{color:#b23b3b}
.tag.human{color:#1f7a4d}
.meta{color:var(--mut);font-size:12px;margin-left:auto}
.text{font-size:15px;white-space:pre-wrap}
mark{background:none;padding:0 1px;border-radius:3px}
mark.pay{background:#ffe08a;color:#3a2e00}
mark.loss{background:#9ae6b4;color:#12351f}
mark.lossword{background:#cdeccd;color:#12351f}
mark.cred{background:#bee3f8;color:#0d2b3e}
mark.money{outline:1px dashed #c8a04a;border-radius:2px}
@media(prefers-color-scheme:dark){
mark.pay{background:#6b5300;color:#ffe9a8}
mark.loss{background:#1f5136;color:#bdf0d0}
mark.lossword{background:#24422c;color:#bdf0d0}
mark.cred{background:#1c3e54;color:#c6e6ff}}
.legend{font-size:12px;color:var(--mut);margin-bottom:18px}
.legend span{margin-right:14px}
</style></head><body><div class="wrap">
<h1>__TITLE__</h1>
<p class="sub">這些是<b>規則判「尚未付款」但你標了別的階段</b>的案例。
問題：你是從哪些字判斷的？<br>
底色只是把幾類線索標出來方便看，<b>不代表規則有用到它們</b>。</p>
<p class="legend">
<span><mark class="pay">付款動詞</mark></span>
<span><mark class="loss">被騙＋金額</mark></span>
<span><mark class="lossword">被騙（無金額）</mark></span>
<span><mark class="cred">帳密／證件／認證</mark></span>
<span><mark class="money">金額</mark></span>
</p>
__BODY__
</div></body></html>
"""


def _mark(text: str) -> str:
    """把線索包上 <mark>。先轉義，再用佔位符避免標記互相吃掉。"""
    out = html.escape(text)
    for cls, pat in MARKS:
        out = re.sub(
            pat,
            lambda m, c=cls: f"\x00{c}\x01{m.group(0)}\x02",
            out,
        )
    out = re.sub(r"\x00(\w+)\x01", lambda m: f'<mark class="{m.group(1)}">', out)
    return out.replace("\x02", "</mark>")


def main() -> int:
    want = sys.argv[1] if len(sys.argv) > 1 else None
    gold = {r["case_id"]: r["stage"] for r in json.loads(GOLD.read_text(encoding="utf-8"))["labels"]}
    sample = {
        json.loads(line)["case_id"]: json.loads(line)
        for line in SAMPLE.read_text(encoding="utf-8").splitlines()
        if line
    }

    picked = [
        (cid, row)
        for cid, row in sample.items()
        if row["rule"] == "none" and gold.get(cid) not in (None, "none")
        and (want is None or gold.get(cid) == want)
    ]
    if not picked:
        print("沒有符合的案例")
        return 1

    blocks = []
    for i, (cid, row) in enumerate(picked, 1):
        safe = deid.mask(row["text"])
        meta = " · ".join(x for x in (row.get("date", ""), row.get("county", ""), row.get("label", "")) if x)
        blocks.append(
            f'<div class="case"><div class="hd">'
            f'<span class="n">{i}</span>'
            f'<span class="tag rule">規則：{NAME[row["rule"]]}</span>'
            f'<span class="tag human">你標：{NAME[gold[cid]]}</span>'
            f'<span class="meta">{html.escape(meta)}</span></div>'
            f'<div class="text">{_mark(safe.text)}</div></div>'
        )

    title = f"規則漏判複查（{len(picked)} 筆"
    title += f"·你標「{NAME[want]}」的" if want else ""
    title += "）"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(PAGE.replace("__TITLE__", title).replace("__BODY__", "\n".join(blocks)), encoding="utf-8")
    print(f"{OUT.relative_to(HERE.parent.parent.parent)}（{len(picked)} 筆）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
