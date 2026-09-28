"""報案資料整理（g_報案資料整理）的測試。

這個工具跟其他模組方向相反：產出要帶著真名、精確金額、完整帳號，
所以不過 shared.deid，也不呼叫任何模型——全程在本機。
"""

from __future__ import annotations

from pathlib import Path

from contracts import RiskLevel, Verdict

from packages.modules.g_報案資料整理.cli import main
from packages.modules.g_報案資料整理.draft import missing_items, render
from packages.modules.g_報案資料整理.schemas import ReportFacts

EXAMPLE = Path(__file__).resolve().parent.parent / (
    "packages/modules/g_報案資料整理/examples/facts.example.yaml"
)


def _facts(**overrides) -> ReportFacts:
    base = {
        "reporter": {"name": "王大明", "phone": "0900-000-000"},
        "summary": "在臉書看到投資廣告，加了對方 LINE 後被拉進群組，匯款後無法出金。",
        "transfers": [
            {
                "time": "2026-09-01 14:30",
                "amount": 30000,
                "method": "轉帳",
                "payee": "000-1234567890123",
                "bank": "範例銀行",
            },
        ],
        "counterparties": [{"kind": "LINE", "value": "@000example"}],
        "evidence": ["LINE 對話截圖"],
    }
    base.update(overrides)
    return ReportFacts.model_validate(base)


# ── 還缺哪些資料 ─────────────────────────────────────────────


def test_資料齊全時沒有缺項():
    assert missing_items(_facts()) == []


def test_轉帳沒寫收款帳號要提醒():
    facts = _facts(transfers=[{"time": "2026-09-01 14:30", "amount": 30000, "method": "轉帳"}])
    assert missing_items(facts) == ["第 1 筆付款缺收款資訊（帳號、超商代碼或錢包地址）"]


def test_現金面交不需要收款帳號():
    facts = _facts(transfers=[{"time": "2026-09-01 14:30", "amount": 30000, "method": "現金面交"}])
    assert missing_items(facts) == []


def test_付款沒寫時間要提醒():
    facts = _facts(transfers=[{"amount": 30000, "method": "轉帳", "payee": "000-1234567890123"}])
    assert missing_items(facts) == ["第 1 筆付款缺時間"]


def test_沒填時間的付款排在最後():
    facts = _facts(
        transfers=[
            {"amount": 20000, "method": "現金面交"},
            {"time": "2026-09-01 14:30", "amount": 30000, "method": "轉帳", "payee": "A"},
        ]
    )
    assert missing_items(facts) == ["第 2 筆付款缺時間"]


def test_沒有對方資訊也沒有證據時兩項都要提醒():
    facts = _facts(counterparties=[], evidence=[])
    assert missing_items(facts) == [
        "沒有對方的聯絡方式（電話、LINE ID、網址、帳號）",
        "沒有列出保存的證據（對話截圖、交易明細）",
    ]


def test_缺項的第幾筆跟付款表格的順序一致():
    # 使用者不一定照時間順序填。缺帳號的那筆是第 2 個填的，但時間最早——
    # 提醒說「第 1 筆」，表格第 1 列就必須是它，不然使用者會補錯筆。
    facts = _facts(
        transfers=[
            {
                "time": "2026-09-05 10:00",
                "amount": 45500,
                "method": "轉帳",
                "payee": "000-1234567890123",
            },
            {"time": "2026-09-01 14:30", "amount": 30000, "method": "轉帳"},
        ]
    )
    assert missing_items(facts) == ["第 1 筆付款缺收款資訊（帳號、超商代碼或錢包地址）"]
    table_rows = [line for line in render(facts).splitlines() if line.startswith("| 1 |")]
    assert len(table_rows) == 1
    assert "30,000" in table_rows[0]


# ── 草稿內容 ─────────────────────────────────────────────────


def test_付款合計是精確金額():
    facts = _facts(
        transfers=[
            {"time": "2026-09-01 14:30", "amount": 30000, "method": "轉帳", "payee": "A"},
            {"time": "2026-09-05 10:00", "amount": 45500, "method": "轉帳", "payee": "B"},
        ]
    )
    assert "合計 NT$75,500" in render(facts)


def test_草稿保留原始帳號不遮蔽():
    # 其他模組都要先過 deid；這裡刻意不過——警察要的就是完整帳號。
    draft = render(_facts())
    assert "000-1234567890123" in draft
    assert "王大明" in draft
    assert "@000example" in draft


def test_有附判讀時寫出詐騙類型與階段():
    verdict = Verdict(
        module_id="c_tbd",
        risk_level=RiskLevel.HIGH,
        scam_type="假投資",
        scam_stage="出金受阻",
    )
    draft = render(_facts(), verdict)
    assert "假投資" in draft
    assert "出金受阻" in draft


def test_沒附判讀時照樣產生草稿():
    draft = render(_facts())
    assert "沒有附上系統判讀" in draft


def test_提醒要拿到受理案件證明單才算完成報案():
    # 很多受害者以為打過 165 就算報案了
    assert "受（處）理案件證明單" in render(_facts())


def test_只填日期的事件不顯示時分():
    # 顯示成 00:00 會讓人以為是半夜發生的
    facts = _facts(events=[{"time": "2026-09-01", "description": "看到廣告"}])
    assert "- 2026-09-01：看到廣告" in render(facts)


def test_缺項清單放在草稿裡():
    facts = _facts(evidence=[])
    assert "沒有列出保存的證據（對話截圖、交易明細）" in render(facts)


# ── 命令列 ───────────────────────────────────────────────────


def test_命令列寫出_utf8_檔案(tmp_path):
    # 繁中 Windows 的 open() 預設 cp950，編不出 emoji。使用者的敘述裡有 emoji
    # 很正常，所以讀寫都必須明確指定 utf-8。
    facts_file = tmp_path / "facts.yaml"
    facts_file.write_text(
        "reporter: {name: 王大明, phone: 0900-000-000}\nsummary: 對方說保證獲利 😀\n",
        encoding="utf-8",
    )
    out = tmp_path / "draft.md"
    assert main([str(facts_file), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "保證獲利 😀" in text
    assert "王大明" in text


def test_命令列可以帶判讀_json(tmp_path):
    facts_file = tmp_path / "facts.yaml"
    facts_file.write_text(
        "reporter: {name: 王大明, phone: 0900-000-000}\nsummary: 經過\n", encoding="utf-8"
    )
    verdict_file = tmp_path / "verdict.json"
    verdict_file.write_text(
        Verdict(module_id="c_tbd", scam_type="假投資", scam_stage="出金受阻").model_dump_json(),
        encoding="utf-8",
    )
    out = tmp_path / "draft.md"
    assert main([str(facts_file), "--verdict", str(verdict_file), "--out", str(out)]) == 0
    assert "出金受阻" in out.read_text(encoding="utf-8")


def test_命令列欄位填錯時不寫檔並回傳錯誤(tmp_path, capsys):
    facts_file = tmp_path / "facts.yaml"
    facts_file.write_text("summary: 只有經過，沒有報案人\n", encoding="utf-8")
    out = tmp_path / "draft.md"
    assert main([str(facts_file), "--out", str(out)]) == 2
    assert not out.exists()
    assert "reporter" in capsys.readouterr().err


def test_範例檔跟得上欄位規格(tmp_path):
    out = tmp_path / "draft.md"
    assert main([str(EXAMPLE), "--out", str(out)]) == 0
