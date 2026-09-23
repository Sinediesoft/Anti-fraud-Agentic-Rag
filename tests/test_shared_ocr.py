"""共用 OCR 的測試：shared.models.ocr()、路由前認字、範本的 M2。

這組測試守的是「同一張圖全隊只認一次」與「認不出來也不能讓任何東西當掉」。
引擎一律用假的 —— S11 還沒鎖定真引擎，而且真引擎在 CI 上也不該裝。
"""

from __future__ import annotations

import dataclasses
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import pytest
from contracts import (
    AnalyzeInput,
    HealthReport,
    ImageInput,
    ModuleInfo,
    PackSpec,
    Plan,
    RiskLevel,
    Verdict,
)
from modules._template import m2_vision
from shared import models

from app.entitlements import Entitlements
from app.registry import LoadedModule
from app.router import route
from app.shell import Shell

# ── 假引擎 ──────────────────────────────────────────────────


@pytest.fixture
def 未鎖定(monkeypatch):
    monkeypatch.setitem(models.MODEL_LOCK, "ocr", models.ModelLock("ocr", "TODO-S11", "", "", ""))
    monkeypatch.setattr(models, "_OCR_ENGINE", ())
    monkeypatch.setattr(models, "_OCR_CACHE", OrderedDict())


@pytest.fixture
def 假引擎(monkeypatch):
    """鎖定一個假引擎。回傳的 dict 記著引擎被載入幾次、認了哪些圖。

    認出來的字就是圖檔內容本身（當成 UTF-8 文字），這樣測試能直接看出
    拿到的是哪一張圖的結果。
    """
    calls: dict[str, list] = {"load": [], "read": []}

    def factory():
        calls["load"].append(1)

        def engine(data: bytes) -> list[models.OcrLine]:
            calls["read"].append(data)
            return [
                models.OcrLine(text=line, bbox=(0, i * 20, 100, i * 20 + 18), score=0.9)
                for i, line in enumerate(data.decode("utf-8").splitlines())
            ]

        return engine

    monkeypatch.setitem(
        models.MODEL_LOCK, "ocr", models.ModelLock("ocr", "fake-ocr", "v1", "-", "2026-09-23")
    )
    monkeypatch.setitem(models.OCR_ENGINES, "fake-ocr", factory)
    monkeypatch.setattr(models, "_OCR_ENGINE", ())
    monkeypatch.setattr(models, "_OCR_CACHE", OrderedDict())
    return calls


def _圖(tmp_path, name: str, content: str):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ── shared.models.ocr() ─────────────────────────────────────


def test_未鎖定時講清楚是S11的工作(tmp_path, 未鎖定):
    with pytest.raises(models.ModelNotSelectedError, match="S11"):
        models.ocr(_圖(tmp_path, "a.png", "字"))
    assert models.ocr_ready() is False


def test_鎖了但還沒接adapter_不能假裝可以用(tmp_path, monkeypatch, 未鎖定):
    monkeypatch.setitem(
        models.MODEL_LOCK, "ocr", models.ModelLock("ocr", "還沒接的引擎", "v1", "-", "2026-09-23")
    )
    assert models.ocr_ready() is False
    with pytest.raises(models.ModelNotSelectedError, match="OCR_ENGINES"):
        models.ocr(_圖(tmp_path, "a.png", "字"))


def test_鎖定又接上時才算可以用(假引擎):
    assert models.ocr_ready() is True


def test_同一張圖只認一次_引擎只載一次(tmp_path, 假引擎):
    path = _圖(tmp_path, "a.png", "老師叫我先入金\n才能出金")
    first = models.ocr(path)
    second = models.ocr(path)
    assert second is first
    assert len(假引擎["read"]) == 1
    assert len(假引擎["load"]) == 1
    assert first.plain_text == "老師叫我先入金\n才能出金"
    assert first.engine == "fake-ocr@v1"


def test_同一個路徑換了內容就要重認(tmp_path, 假引擎):
    # app/ui.py 用原始檔名寫暫存檔：兩張都叫 image.png 的圖會落在同一個路徑
    path = _圖(tmp_path, "image.png", "第一張")
    assert models.ocr(path).plain_text == "第一張"
    path.write_text("第二張", encoding="utf-8")
    assert models.ocr(path).plain_text == "第二張"


def test_內容一樣路徑不同_不重認(tmp_path, 假引擎):
    models.ocr(_圖(tmp_path, "a.png", "同一張"))
    models.ocr(_圖(tmp_path, "b.png", "同一張"))
    assert len(假引擎["read"]) == 1


def test_引擎換版時快取跟著失效(tmp_path, monkeypatch, 假引擎):
    path = _圖(tmp_path, "a.png", "字")
    models.ocr(path)
    monkeypatch.setitem(
        models.MODEL_LOCK, "ocr", models.ModelLock("ocr", "fake-ocr", "v2", "-", "2026-09-24")
    )
    assert models.ocr(path).engine == "fake-ocr@v2"
    assert len(假引擎["read"]) == 2
    assert len(假引擎["load"]) == 2


def test_快取有上限_原文不會越積越多(tmp_path, monkeypatch, 假引擎):
    monkeypatch.setattr(models, "OCR_CACHE_SIZE", 2)
    for i in range(3):
        models.ocr(_圖(tmp_path, f"{i}.png", f"第{i}張"))
    assert len(models._OCR_CACHE) == 2


def test_結果不能被改_因為好幾個模組共用同一份(tmp_path, 假引擎):
    result = models.ocr(_圖(tmp_path, "a.png", "字"))
    assert isinstance(result.lines, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.lines[0].text = "被改掉"  # type: ignore[misc]


def test_並行要同一張圖時也只認一次(tmp_path, 假引擎):
    # analyze_all() 會並行跑認領的模組，它們要的是同一張圖
    path = _圖(tmp_path, "a.png", "字")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: models.ocr(path), range(8)))
    assert len(假引擎["read"]) == 1
    assert all(r is results[0] for r in results)


def test_空白行不進純文字(tmp_path, 假引擎):
    assert models.ocr(_圖(tmp_path, "a.png", "甲\n  \n乙")).plain_text == "甲\n乙"


# ── 路由前先認字 ────────────────────────────────────────────


class _看圖的假模組:
    """can_handle() 與 analyze() 都去拿截圖上的字，跟範本的寫法一樣。"""

    def __init__(self, module_id: str, score: float):
        self._id = module_id
        self._score = score
        self.seen: list[str] = []

    def _look(self, payload: AnalyzeInput) -> None:
        for image in payload.images:
            try:
                self.seen.append(models.ocr(image.path).plain_text)
            except models.ModelNotSelectedError:
                self.seen.append("")

    def can_handle(self, payload: AnalyzeInput) -> float:
        self._look(payload)
        return self._score

    def analyze(self, payload: AnalyzeInput) -> Verdict:
        from contracts import ActionItem

        self._look(payload)
        return Verdict(
            module_id=self._id,
            risk_level=RiskLevel.HIGH,
            scam_type="測試類型",
            actions=[ActionItem(order=1, text="立即撥打 165 並聯繫匯款銀行申請圈存")],
        )

    def info(self) -> ModuleInfo:
        return ModuleInfo(id=self._id, code="X", name=f"假模組{self._id}", plan=Plan.PAID)

    def health(self) -> HealthReport:
        return HealthReport(module_id=self._id, ready=True)


def _wrap(instance) -> LoadedModule:
    pack = PackSpec(
        id=instance.info().id,
        code="X",
        name=instance.info().name,
        plan=Plan.PAID,
        platform="測試平台",
        tactic="測試手法",
        labels_canon=["測試"],
    )
    return LoadedModule(pack=pack, instance=instance, health=instance.health(), path=None)  # type: ignore[arg-type]


class _假註冊表:
    def __init__(self, modules):
        self.loaded = modules

    @property
    def usable(self):
        return self.loaded

    def get(self, module_id):
        return next((m for m in self.loaded if m.id == module_id), None)


def _有圖(tmp_path, content: str = "保證獲利") -> AnalyzeInput:
    return AnalyzeInput(text="", images=[ImageInput(path=str(_圖(tmp_path, "s.png", content)))])


def test_沒有圖時不多記一筆(假引擎):
    reg = _假註冊表([_wrap(_看圖的假模組("m1", 0.9))])
    decision = route(reg, AnalyzeInput(text="老師叫我先入金"))
    assert [e.step for e in decision.trace] == ["route:m1"]
    assert 假引擎["read"] == []


def test_五個模組都看圖_全隊只認一次(tmp_path, 假引擎):
    mods = [_看圖的假模組(f"m{i}", 0.1 * i) for i in range(1, 6)]
    decision = route(_假註冊表([_wrap(m) for m in mods]), _有圖(tmp_path))

    assert len(假引擎["read"]) == 1
    assert all(m.seen == ["保證獲利"] for m in mods)
    assert decision.trace[0].step == "route:ocr"
    assert decision.trace[0].status == "ok"


def test_並行判讀的模組也拿快取(tmp_path, 假引擎):
    mods = [_看圖的假模組("a", 0.9), _看圖的假模組("b", 0.8)]
    shell = Shell(
        registry=_假註冊表([_wrap(m) for m in mods]), entitlements=Entitlements(unlocked=True)
    )

    responses = shell.analyze_all(_有圖(tmp_path))

    assert len(responses) == 2
    # 路由時兩個各看一次、判讀時又各看一次 —— 引擎還是只跑一次
    assert all(len(m.seen) == 2 for m in mods)
    assert len(假引擎["read"]) == 1


def test_認不出字不擋路由(tmp_path, 未鎖定):
    reg = _假註冊表([_wrap(_看圖的假模組("m1", 0.9))])
    decision = route(reg, _有圖(tmp_path))

    assert decision.primary.id == "m1"
    event = decision.trace[0]
    assert event.step == "route:ocr"
    assert event.status == "degraded"
    assert "0/1" in event.detail


def test_手動切換也先認字(tmp_path, 假引擎):
    reg = _假註冊表([_wrap(_看圖的假模組("a", 0.0))])
    decision = route(reg, _有圖(tmp_path), manual="a")
    assert [e.step for e in decision.trace] == ["route:ocr", "route:manual"]
    assert len(假引擎["read"]) == 1


# ── 範本的 M2 ───────────────────────────────────────────────


def test_範本M2_沒有OCR時降級成只看打字的內容(tmp_path, 未鎖定):
    read = m2_vision.read_screenshot(ImageInput(path=str(_圖(tmp_path, "a.png", "字"))))
    assert read.degraded
    assert read.blocks == []
    assert "只用打字的內容" in read.reason


def test_範本M2_圖檔不見也不當掉(tmp_path, 假引擎):
    read = m2_vision.read_screenshot(ImageInput(path=str(tmp_path / "不存在.png")))
    assert read.degraded
    assert read.plain_text == ""


def test_範本M2_有OCR時字交得出去_座標也在(tmp_path, 假引擎):
    read = m2_vision.read_screenshot(
        ImageInput(path=str(_圖(tmp_path, "a.png", "老師說\n保證獲利")))
    )
    assert read.plain_text == "老師說\n保證獲利"
    assert read.blocks[1].bbox == (0, 20, 100, 38)
    # 版面判斷還沒寫，所以照實標成降級，不假裝知道誰說的
    assert read.degraded
    assert all(b.speaker == "unknown" for b in read.blocks)
