"""註冊表的測試：掃得到、載得起來、底線開頭要略過。"""

from __future__ import annotations

from contracts import AnalyzeInput

from app.registry import discover, load


def test_掃描時略過底線開頭的範本模組():
    names = [p.name for p in discover()]
    assert "_template" not in names
    assert "a_tbd" in names


def test_範本模組要指名才載得到():
    assert load("_template").get("_template") is not None


def test_模組_a_載得起來而且四個進入點都在():
    reg = load("a_tbd")
    assert not reg.failures
    mod = reg.get("a_tbd")
    assert mod is not None
    info = mod.instance.info()
    assert info.code == "A"
    assert info.plan.value == "free"


def test_載入全部時不會有失敗():
    reg = load("all")
    assert not reg.failures, reg.failures


def test_未設定組合的模組仍然載得起來():
    """空置狀態下架構要能跑，只是不認領案子。

    這條壞過兩次，每次都是同一個原因：它拿「目前還沒填題目的那個人」當例子。
    先是 a_tbd（2026-09-20 填了 LINE x 假投資），改成 e_tbd 之後又被 PR #41
    填上 Threads x 網路購物詐騙。現在五個人都宣告完了，沒有模組是空的 ——
    再挑一個真人的模組，下次還是會壞。

    所以改成用 _template：它是凍結的共用範本、不是任何人的題目。把它的 pack
    換成空的來驗「空著就不認領」那條路還通，而不是去依賴誰還沒填。
    斷言也從 is_configured 換成真正的行為後果（can_handle 回 0）——
    契約層的 is_configured 語意在 test_contracts.py 已經有測了。
    """
    mod = load("_template").get("_template")
    assert mod is not None
    assert mod.usable  # 載得起來

    blank = mod.pack.model_copy(update={"platform": "", "tactic": "", "labels_canon": []})
    assert blank.is_configured is False
    mod.instance.pack = blank
    # 一句標準的假投資敘述：組合填著的時候會拿到分數，空著就必須是 0
    assert mod.instance.can_handle(AnalyzeInput(text="群組裡的老師叫我先入金才能出金")) == 0.0


def test_找不到的模組回報失敗而不是爆炸():
    reg = load("不存在的模組")
    assert reg.failures and not reg.loaded
