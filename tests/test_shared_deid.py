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


def test_年份不可以被當成金額遮掉():
    """2026-09-21 在 165 語料上抓到的：「我在2023年12月初」被遮成
    「我在1 萬以下年12月初」，而且直接顯示在使用者看得到的相似案例節錄上。

    四位數的西元年剛好落在金額規則的 \\d{4,12} 裡，三位數的民國年因為
    長度守門躲過去了 —— 所以這個 bug 只在西元年上出現，民國年看不到。
    下面列的是 165 的自由敘述裡實際出現的主流寫法。
    """
    for text in [
        "我在2023年12月初看到廣告",
        "我於2024 年 11 月在臉書看到",
        "西元2023年",
        "民國113年12月",
        "民國98年那時候",
        "113年12月初",
        "112年度的報稅",
        "2023/12/31 匯款",
        "2023-12-31 那天",
        "2023.12.31",
    ]:
        assert deid.mask(text).text == text, f"{text} 的年份被當成金額了"


def test_修年份不能順手漏掉金額():
    """上面那條的反面 —— 排除年份時很容易把金額一起放過。"""
    assert "500 萬以上" in deid.mask("損失高達1000萬元").text
    assert "5 萬到 10 萬" in deid.mask("我匯了73500元過去").text
    assert "1 萬以下" in deid.mask("轉帳 1,234 元").text  # 有千分位就一定是金額
    assert "5 萬到 10 萬" in deid.mask("存了50000進去").text  # 沒幣別字也要遮
    # 同一句話裡年份留著、金額遮掉
    out = deid.mask("2023年匯了50000元").text
    assert "2023年" in out and "5 萬到 10 萬" in out


def test_金額分桶():
    assert deid.amount_to_range(5_000) == "1 萬以下"
    assert deid.amount_to_range(73_500) == "5 萬到 10 萬"
    assert deid.amount_to_range(9_000_000) == "500 萬以上"
