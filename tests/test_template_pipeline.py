"""範本模組的流水線測試。

五個人都從這份骨架複製過去，所以它的行為要是對的 ——
特別是「判錯要往高風險的方向錯」這件事。
"""

from __future__ import annotations

from contracts import AnalyzeInput, CoverageStatus, RiskLevel
from modules._template import m4_judgement

from app.entitlements import Entitlements
from app.registry import load
from app.shell import Shell

STAGES = [
    {"id": "contacted", "cues": ["收到", "通知", "簡訊"]},
    {"id": "paying", "cues": ["手續費", "稅金"]},
    {"id": "paid", "cues": ["匯了", "已匯"]},
]


def test_關鍵詞計分不隨關鍵詞數量稀釋():
    text = "我抽中大獎要繳手續費"
    少 = m4_judgement.keyword_score(text, ["抽中", "手續費"], [])
    多 = m4_judgement.keyword_score(text, ["抽中", "手續費", "兌獎", "領獎", "稅金", "得獎"], [])
    # 關鍵詞列得更完整不該讓分數變低，否則會逼人為了分數少列關鍵詞
    assert 少 == 多


def test_反向關鍵詞會扣分():
    有 = m4_judgement.keyword_score("中獎 手續費", ["中獎", "手續費"], [])
    扣 = m4_judgement.keyword_score("中獎 手續費 投資", ["中獎", "手續費"], ["投資"])
    assert 扣 < 有


def test_階段取有命中的裡面走得最遠的():
    # 受害者常把整段經過講完，命中最多的往往是最前面那一階段
    text = "我收到簡訊說中獎，要繳手續費，後來我匯了錢"
    assert m4_judgement.detect_stage(text, STAGES) == "paid"


def test_沒有命中時回空字串交給_fallback():
    assert m4_judgement.detect_stage("完全無關的一段話", STAGES) == ""


def test_規則抽取把金額換成範圍而不是原值():
    profile = m4_judgement.extract_profile(
        "我匯了73,500元", platform_terms=["簡訊"], stage_id="paid"
    )
    assert profile.amount_range == "5 萬到 10 萬"


def test_明顯的案子會被認領且走到最嚴重的階段():
    shell = Shell(load("_template"), Entitlements(unlocked=True))
    res = shell.analyze(
        AnalyzeInput(text="有人傳簡訊說我抽中大獎，要先繳手續費才能領，我已經匯了73,500元")
    )
    assert res.coverage is CoverageStatus.COVERED
    assert res.risk_level is RiskLevel.CRITICAL
    assert res.verdict.actions
    assert "165" in res.verdict.actions[0].text


def test_高風險階段一定有預防性提示以外的立即行動():
    shell = Shell(load("_template"), Entitlements(unlocked=True))
    verdict = shell.analyze(
        AnalyzeInput(text="收到簡訊說我中獎要先繳稅金，我已經匯了五萬元給對方")
    ).verdict
    assert any(a.urgency == "now" and not a.preventive for a in verdict.actions)


def test_證據不足時保守不認領():
    # 不確定就給低分，讓外殼判定「尚未涵蓋」—— 給錯的建議比說不知道糟得多
    module = load("_template").get("_template").instance
    assert module.can_handle(AnalyzeInput(text="今天天氣很好")) < 0.35
