"""註冊表的測試：掃得到、載得起來、底線開頭要略過。"""

from __future__ import annotations

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
    # 空置狀態下架構要能跑，只是不認領案子
    mod = load("a_tbd").get("a_tbd")
    assert mod is not None
    assert mod.usable
    assert mod.pack.is_configured is False


def test_找不到的模組回報失敗而不是爆炸():
    reg = load("不存在的模組")
    assert reg.failures and not reg.loaded
