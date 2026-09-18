"""去識別化 —— 共用三樣之一，不准自己寫（說明書 S5 第 1 點）。

原則：寧可遮太多也不要遮太少。遮太多最多體驗差，遮太少是把受害者個資送出去。
要求抓出率 95% 以上。

做法是規則比對為主、NER 輔助。NER 要等 S3 鎖定模型之後才接得上，
所以現在是純規則版 —— 但規則版本身就必須達標，NER 只是補強。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from contracts import MaskedText, Redaction

# 規則版本。每次改規則就要加版號，因為分數表要記「當時用哪一版遮的」
DEID_VERSION = "rules-2026.09.1"


@dataclass(frozen=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    placeholder: str


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# 順序有意義：先抓長的、格式特殊的，再抓短的、容易誤傷的。
# 超商代碼（11~13 碼）先抓走，才不會被後面的銀行帳號規則誤判 ——
# 這是說明書 §1.7 舉的那個真實 Issue 例子。
_RULES: tuple[_Rule, ...] = (
    _Rule("email", _p(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    _Rule("url", _p(r"https?://[^\s，。、）)]+"), "[URL]"),
    _Rule(
        "national_id",
        _p(r"(?<![A-Za-z0-9])[A-Za-z][12]\d{8}(?![0-9])"),
        "[身分證]",
    ),
    _Rule(
        "cvs_code",
        _p(r"(?:超商代碼|繳費代碼|條碼|代碼)\s*[:：]?\s*([A-Za-z0-9]{10,16})"),
        "[超商代碼]",
    ),
    _Rule(
        "credit_card",
        _p(r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)"),
        "[卡號]",
    ),
    _Rule(
        "mobile",
        _p(r"(?<!\d)09\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)"),
        "[手機]",
    ),
    _Rule(
        "landline",
        _p(r"(?<!\d)0\d{1,2}[-\s]?\d{6,8}(?!\d)"),
        "[電話]",
    ),
    _Rule(
        "bank_account",
        _p(r"(?:帳號|匯[到入至]|轉[到入至]|戶號)\s*[:：]?\s*(\d[\d\- ]{8,20}\d)"),
        "[帳號]",
    ),
    _Rule(
        "bare_account",
        _p(r"(?<!\d)\d{10,16}(?!\d)"),
        "[帳號]",
    ),
    _Rule(
        "line_id",
        _p(r"(?:LINE|line|賴|加賴)\s*(?:ID|id|帳號)?\s*[:：]\s*([A-Za-z0-9_.-]{3,30})"),
        "[通訊帳號]",
    ),
    _Rule(
        "person",
        # NER 接上來之前的規則替代品：稱謂與自我介紹句型
        _p(r"(?:我叫|我是|姓名是|本人|收款人|匯款人|對方叫|自稱)\s*([一-鿿]{2,4})(?=[，。、\s]|$)"),
        "[姓名]",
    ),
    _Rule(
        "address",
        _p(r"[一-鿿]{2,3}[市縣][一-鿿]{1,3}[區鄉鎮市][一-鿿0-9]{2,12}[路街道][^\s，。]{0,12}號"),
        "[地址]",
    ),
)

# 金額：不遮掉，改成範圍。「73,500 元」→「5 萬到 10 萬」
_AMOUNT = _p(r"(?<![\d,])((?:\d{1,3}(?:,\d{3})+|\d{4,12}))\s*(?:元|塊|NTD?|台幣)?")
_AMOUNT_CN = _p(r"(\d+(?:\.\d+)?)\s*(萬|億)\s*(?:元|塊)?")

_BUCKETS: tuple[tuple[int, str], ...] = (
    (10_000, "1 萬以下"),
    (50_000, "1 萬到 5 萬"),
    (100_000, "5 萬到 10 萬"),
    (500_000, "10 萬到 50 萬"),
    (1_000_000, "50 萬到 100 萬"),
    (5_000_000, "100 萬到 500 萬"),
)


def amount_to_range(value: float) -> str:
    """把精確金額換成範圍。分數比較與展示都用這個，不用原值。"""
    for ceiling, label in _BUCKETS:
        if value < ceiling:
            return label
    return "500 萬以上"


def _mask_amounts(text: str, redactions: list[Redaction]) -> str:
    def repl_cn(m: re.Match[str]) -> str:
        unit = 10_000 if m.group(2) == "萬" else 100_000_000
        label = amount_to_range(float(m.group(1)) * unit)
        redactions.append(Redaction(kind="amount", start=m.start(), end=m.end(), placeholder=label))
        return label

    text = _AMOUNT_CN.sub(repl_cn, text)

    def repl(m: re.Match[str]) -> str:
        raw = m.group(1).replace(",", "")
        # 四位數以下又沒有千分位的多半是年份或編號，不是金額，留著
        if "," not in m.group(1) and len(raw) < 4:
            return m.group(0)
        label = amount_to_range(float(raw))
        redactions.append(Redaction(kind="amount", start=m.start(), end=m.end(), placeholder=label))
        return label

    return _AMOUNT.sub(repl, text)


def mask(text: str) -> MaskedText:
    """把一段原文遮成可以往外送的樣子。

    這是「原文不進雲端」那條界線的起點：只有這個函式能產生 MaskedText，
    而 shared.models 的雲端呼叫只收 MaskedText。
    """
    redactions: list[Redaction] = []
    masked = text

    for rule in _RULES:

        def repl(m: re.Match[str], rule: _Rule = rule) -> str:
            redactions.append(
                Redaction(
                    kind=rule.kind,
                    start=m.start(),
                    end=m.end(),
                    placeholder=rule.placeholder,
                )
            )
            # 有捕獲群組的規則只遮那一段，保留前面的提示詞（「帳號：[帳號]」比「[帳號]」好讀）
            if rule.pattern.groups:
                return m.group(0).replace(m.group(1), rule.placeholder)
            return rule.placeholder

        masked = rule.pattern.sub(repl, masked)

    masked = _mask_amounts(masked, redactions)

    return MaskedText(text=masked, redactions=redactions, deid_version=DEID_VERSION)


def is_masked(value: object) -> bool:
    """給 shared.models 的守門員用。"""
    return isinstance(value, MaskedText) and bool(value.deid_version)


def recall_rate(text: str, expected_kinds: list[str]) -> float:
    """量抓出率用的小工具（要求 ≥ 0.95）。

    給 shared.eval 的去識別化測試呼叫：丟一段已知含哪些個資的文字，
    看規則抓到幾種。
    """
    if not expected_kinds:
        return 1.0
    found = {r.kind for r in mask(text).redactions}
    # bare_account 與 bank_account 視為同一種
    normalized = {"bank_account" if k == "bare_account" else k for k in found}
    hit = sum(1 for k in expected_kinds if k in normalized)
    return hit / len(expected_kinds)
