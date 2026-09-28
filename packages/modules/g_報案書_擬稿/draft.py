"""把報案資料整理成 Markdown。純範本，不呼叫模型。"""

from __future__ import annotations

from datetime import datetime

from contracts import DISCLAIMER, RiskLevel, Verdict

from .schemas import ReportFacts, Transfer

# 這幾種付款方式一定有收款端可以追：帳號、超商代碼或錢包地址
_PAYEE_REQUIRED = {"轉帳", "ATM", "網路銀行", "超商代碼", "虛擬貨幣"}

_RISK_ZH = {
    RiskLevel.UNKNOWN: "未知",
    RiskLevel.LOW: "低",
    RiskLevel.MEDIUM: "中",
    RiskLevel.HIGH: "高",
    RiskLevel.CRITICAL: "極高",
}

_PRIVACY_NOTE = (
    "這份資料含有你的真實個資，只存在你自己的電腦上。"
    "不要上傳到網路，也不要傳給自稱能幫你追回款項的人。"
)


def _ordered(transfers: list[Transfer]) -> list[Transfer]:
    """照時間排；沒填時間的排最後，維持填寫順序。

    缺項提醒的「第幾筆」與表格的列號都用這個順序——兩邊不一致，使用者會補錯筆。
    """
    return sorted(transfers, key=lambda t: (t.time is None, t.time or datetime.min))


def _fmt_time(t: datetime | None) -> str:
    if t is None:
        return "（未填）"
    # 只填日期的會被解析成 00:00，照樣顯示會讓人以為是半夜
    return f"{t:%Y-%m-%d}" if (t.hour, t.minute) == (0, 0) else f"{t:%Y-%m-%d %H:%M}"


def missing_items(facts: ReportFacts) -> list[str]:
    items: list[str] = []
    for i, t in enumerate(_ordered(facts.transfers), start=1):
        if t.time is None:
            items.append(f"第 {i} 筆付款缺時間")
        if t.method in _PAYEE_REQUIRED and not t.payee:
            items.append(f"第 {i} 筆付款缺收款資訊（帳號、超商代碼或錢包地址）")
    if not facts.counterparties:
        items.append("沒有對方的聯絡方式（電話、LINE ID、網址、帳號）")
    if not facts.evidence:
        items.append("沒有列出保存的證據（對話截圖、交易明細）")
    return items


def _bullets(values: list[str], empty: str) -> list[str]:
    return [f"- {v}" for v in values] if values else [empty]


def render(facts: ReportFacts, verdict: Verdict | None = None) -> str:
    lines = [
        "# 報案資料整理",
        "",
        "> 這是報案前的資料整理，不是正式文件——正式紀錄是警察製作的筆錄。"
        "報案時請帶著這份資料和證據到派出所。",
        ">",
        "> 只打 165 或只做網路報案，不算完成報案。"
        "要拿到警察給的「受（處）理案件證明單」才算，請記得索取並保存。",
        "",
        "## 還缺的資料",
        "",
    ]
    todo = missing_items(facts)
    lines += [f"- [ ] {item}" for item in todo] if todo else ["必填資料都齊了。"]

    lines += [
        "",
        "## 報案人",
        "",
        f"- 姓名：{facts.reporter.name}",
        f"- 聯絡電話：{facts.reporter.phone}",
        "",
        "## 系統判讀（僅供參考）",
        "",
    ]
    if verdict:
        lines += [
            f"- 詐騙類型：{verdict.scam_type or '（未判定）'}",
            f"- 目前階段：{verdict.scam_stage or '（未判定）'}",
            f"- 風險等級：{_RISK_ZH[verdict.risk_level]}",
            f"- 判讀模組：{verdict.module_id}",
        ]
    else:
        lines.append("沒有附上系統判讀。")

    lines += ["", "## 事情經過", "", facts.summary]

    if facts.events:
        lines += ["", "## 時間軸", ""]
        lines += [
            f"- {_fmt_time(e.time)}：{e.description}"
            for e in sorted(facts.events, key=lambda e: e.time)
        ]

    transfers = _ordered(facts.transfers)
    total = sum(t.amount for t in transfers)
    lines += ["", f"## 付款紀錄（共 {len(transfers)} 筆，合計 NT${total:,}）", ""]
    if transfers:
        lines += [
            "| # | 時間 | 金額（NT$） | 方式 | 收款資訊 | 銀行 | 備註 |",
            "|---|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {i} | {_fmt_time(t.time)} | {t.amount:,} | {t.method} "
            f"| {t.payee or '（未填）'} | {t.bank or ''} | {t.note or ''} |"
            for i, t in enumerate(transfers, start=1)
        ]
    else:
        lines.append("沒有付款紀錄。")

    lines += ["", "## 對方資訊", ""]
    lines += _bullets([f"{c.kind}：{c.value}" for c in facts.counterparties], "（未填）")
    lines += ["", "## 已保存的證據", ""]
    lines += _bullets(facts.evidence, "（未填）")
    lines += ["", "## 已經做的處理", ""]
    lines += _bullets(facts.actions_taken, "（未填）")

    lines += ["", "---", "", DISCLAIMER, "", _PRIVACY_NOTE, ""]
    return "\n".join(lines)
