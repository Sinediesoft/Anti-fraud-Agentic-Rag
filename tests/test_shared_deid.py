"""去識別化的測試。抓出率要 ≥ 0.95，而且寧可遮太多也不要遮太少。"""

from __future__ import annotations

from shared import deid

SAMPLE = (
    "我叫陳小明，身分證A123456789，手機0912-345-678，"
    "他叫我匯到帳號：1234567890123，我已經匯了73,500元，"
    "他的信箱是scam@example.com，還傳了 https://fake-bank.example.com 給我"
)


def test_各種個資都被遮掉():
    out = deid.mask(SAMPLE)
    assert "A123456789" not in out.text
    assert "0912-345-678" not in out.text
    assert "1234567890123" not in out.text
    assert "scam@example.com" not in out.text
    assert "fake-bank.example.com" not in out.text
    assert "陳小明" not in out.text


def test_金額改成範圍而不是遮掉():
    out = deid.mask("我匯了73,500元過去")
    assert "73,500" not in out.text
    assert "5 萬到 10 萬" in out.text


def test_中文金額也要處理():
    assert "10 萬到 50 萬" in deid.mask("被騙了30萬").text


def test_抓出率達標():
    kinds = ["person", "national_id", "mobile", "bank_account", "email", "url", "amount"]
    assert deid.recall_rate(SAMPLE, kinds) >= 0.95


def test_超商代碼不會被當成銀行帳號():
    # §1.7 舉的那個真實 Issue：超商代碼被誤判成帳號
    out = deid.mask("他給我超商代碼：AB12345678901 要我去繳費")
    assert any(r.kind == "cvs_code" for r in out.redactions)


def test_產出帶版本號():
    assert deid.mask("測試").deid_version == deid.DEID_VERSION


def test_金額分桶():
    assert deid.amount_to_range(5_000) == "1 萬以下"
    assert deid.amount_to_range(73_500) == "5 萬到 10 萬"
    assert deid.amount_to_range(9_000_000) == "500 萬以上"
