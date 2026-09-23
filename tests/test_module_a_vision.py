"""模組 A 的截圖理解（M2 / S11）。

引擎是 rapidocr 3.9.2 內建的 PP-OCRv6 small。CI 只裝 dev 那組、沒有 rapidocr，
所以要真引擎的測試用 importorskip 跳過；其餘用假引擎代替「rapidocr 本身」
那一層 —— 快取、去重、降級、路由接不接得上，這些我們自己的邏輯照樣是真的在跑。
"""

from __future__ import annotations

import collections
from pathlib import Path
from types import SimpleNamespace

import pytest
from contracts import AnalyzeInput, ImageInput
from modules.a_tbd import m2_vision

from app.registry import load

REPO_ROOT = Path(__file__).resolve().parent.parent
# e_tbd 的 20 張是全隊唯一有標註的截圖（標註與畫面同源）。這張簡訊裡的「帳戶」
# 「暫停」「攜帶」「開戶」正是 PP-OCRv4 會認成簡體的字 —— 換掉 v4 的理由。
E17 = REPO_ROOT / "packages/modules/e_tbd/screenshots/e17_sms_bank_alert.png"


@pytest.fixture(autouse=True)
def _乾淨的引擎狀態(monkeypatch):
    """每個測試都從「還沒載過引擎、快取是空的」開始，互不污染。"""
    monkeypatch.setattr(m2_vision, "_ENGINE", None)
    monkeypatch.setattr(m2_vision, "_CACHE", collections.OrderedDict())


class _假引擎:
    """代替 rapidocr.RapidOCR。回傳的欄位照真的 RapidOCROutput：
    沒偵測到字時 txts／boxes／scores 都是 None，不是空 tuple。"""

    def __init__(self, lines: list[tuple[str, list[list[float]]]], *, fail: bool = False):
        self.lines = lines
        self.fail = fail
        self.calls = 0

    def __call__(self, img):
        self.calls += 1
        if self.fail:
            raise RuntimeError("測試刻意製造的辨識失敗")
        if not self.lines:
            return SimpleNamespace(boxes=None, txts=None, scores=None)
        return SimpleNamespace(
            boxes=[box for _, box in self.lines],
            txts=tuple(text for text, _ in self.lines),
            scores=tuple(0.99 for _ in self.lines),
        )


def _框(x0, y0, x1, y1) -> list[list[float]]:
    """rapidocr 的文字框是四個角：左上、右上、右下、左下。"""
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _用假引擎(monkeypatch, engine: _假引擎) -> _假引擎:
    monkeypatch.setattr(m2_vision, "_make_engine", lambda: engine)
    return engine


def _圖(tmp_path: Path, name: str, content: bytes) -> ImageInput:
    path = tmp_path / name
    path.write_bytes(content)
    return ImageInput(path=str(path), filename=name)


def _module():
    return load("a_tbd").get("a_tbd").instance


# ── 真引擎 ──────────────────────────────────────────────────────────


def test_截圖上的繁體字要照原樣讀出來():
    pytest.importorskip("rapidocr")
    if not E17.exists():
        pytest.skip("e_tbd 的標註截圖不在（本機沒有那個資料夾）")

    screen = m2_vision.read_screenshot(ImageInput(path=str(E17), filename=E17.name))

    assert screen.degraded is False
    assert "帳戶" in screen.plain_text
    assert "帐户" not in screen.plain_text  # PP-OCRv4 就是在這裡出錯
    assert "暫停" in screen.plain_text


def test_OCR版本不對時health要講出來(monkeypatch):
    pytest.importorskip("rapidocr")
    # 模型檔包在套件裡，版本不同就是模型不同 —— 跟 MODEL_LOCK 釘 digest 同一個道理
    monkeypatch.setattr(m2_vision, "RAPIDOCR_VERSION", "0.0.0")

    ok, detail = m2_vision.engine_status()

    assert ok is False
    assert "0.0.0" in detail


# ── 假引擎：我們自己的邏輯 ──────────────────────────────────────────


def test_辨識出來的每一行都變成一個文字塊(monkeypatch, tmp_path):
    _用假引擎(
        monkeypatch,
        _假引擎([("老師說保證獲利", _框(10, 20, 110, 50)), ("加LINE", _框(12, 60, 70, 88))]),
    )

    screen = m2_vision.read_screenshot(_圖(tmp_path, "a.png", b"image-a"))

    assert screen.degraded is False
    assert [b.text for b in screen.blocks] == ["老師說保證獲利", "加LINE"]
    assert screen.blocks[0].bbox == (10, 20, 110, 50)  # 左上 x、y，右下 x、y
    assert screen.plain_text == "老師說保證獲利\n加LINE"


def test_同一張圖不管傳幾次都只辨識一次(monkeypatch, tmp_path):
    # 路由每一輪都會問每個模組 can_handle()，而外殼每次送出都會把同一張圖再加一次
    # —— 沒有快取的話，一段對話同一張圖會被辨識十幾次。
    engine = _用假引擎(monkeypatch, _假引擎([("出金", _框(0, 0, 10, 10))]))
    first = _圖(tmp_path, "a.png", b"same-bytes")
    copy = _圖(tmp_path, "a-copy.png", b"same-bytes")  # 內容一樣、路徑不同

    for image in (first, copy, first):
        assert m2_vision.read_screenshot(image).plain_text == "出金"

    assert engine.calls == 1


def test_同一個檔名換了內容就要重新辨識(monkeypatch, tmp_path):
    # 外殼把上傳檔寫到 暫存目錄/原檔名 —— 兩次上傳同名的不同截圖會落在同一個路徑。
    # 快取若用路徑當鍵，第二張會拿到第一張的字。
    engine = _用假引擎(monkeypatch, _假引擎([("第一張", _框(0, 0, 10, 10))]))
    image = _圖(tmp_path, "screenshot.png", b"first-upload")
    assert m2_vision.read_screenshot(image).plain_text == "第一張"

    engine.lines = [("第二張", _框(0, 0, 10, 10))]
    Path(image.path).write_bytes(b"second-upload")

    assert m2_vision.read_screenshot(image).plain_text == "第二張"
    assert engine.calls == 2


def test_重複上傳的同一張圖只算一次(monkeypatch, tmp_path):
    # 同一張圖的字重複兩次，只會讓檢索的問句被塞滿同樣的字
    _用假引擎(monkeypatch, _假引擎([("字", _框(0, 0, 10, 10))]))
    a = _圖(tmp_path, "a.png", b"image-a")
    a_again = _圖(tmp_path, "a-again.png", b"image-a")
    b = _圖(tmp_path, "b.png", b"image-b")

    screens = m2_vision.read_all([a, a_again, b])

    assert len(screens) == 2


def test_讀不到的截圖要降級不能當掉(tmp_path):
    missing = ImageInput(path=str(tmp_path / "不存在.png"), filename="不存在.png")

    screen = m2_vision.read_screenshot(missing)

    assert screen.degraded is True
    assert screen.reason
    assert screen.blocks == []


def test_辨識失敗時降級不能當掉(monkeypatch, tmp_path):
    _用假引擎(monkeypatch, _假引擎([], fail=True))

    screen = m2_vision.read_screenshot(_圖(tmp_path, "a.png", b"image-a"))

    assert screen.degraded is True
    assert screen.reason
    assert screen.blocks == []


def test_辨識失敗的結果不會被快取住(monkeypatch, tmp_path):
    # 一次偶發的失敗若被記住，那張圖這個 process 裡就再也讀不到了
    engine = _用假引擎(monkeypatch, _假引擎([("出金", _框(0, 0, 10, 10))], fail=True))
    image = _圖(tmp_path, "a.png", b"image-a")
    assert m2_vision.read_screenshot(image).degraded is True

    engine.fail = False

    assert m2_vision.read_screenshot(image).plain_text == "出金"


def test_沒裝OCR時要降級並說清楚怎麼裝(monkeypatch, tmp_path):
    monkeypatch.setattr(m2_vision, "_PACKAGE", "rapidocr_測試用_不存在")

    ok, detail = m2_vision.engine_status()
    screen = m2_vision.read_screenshot(_圖(tmp_path, "a.png", b"image-a"))

    assert ok is False
    assert "--extra ocr" in detail
    assert screen.degraded is True
    assert screen.blocks == []


def test_沒裝OCR時health說不能用但不擋啟動(monkeypatch):
    monkeypatch.setattr(m2_vision, "_PACKAGE", "rapidocr_測試用_不存在")

    report = _module().health()
    ocr = {c.name: c for c in report.checks}["ocr"]

    assert ocr.ok is False
    assert report.ready is True  # 截圖只是加分，沒有 OCR 照樣能用打字的內容判讀


def test_只有截圖沒打字也能認領(monkeypatch, tmp_path):
    # 路由看得到截圖上的字，才是「直接上傳截圖」這條路真的接上了
    _用假引擎(
        monkeypatch,
        _假引擎(
            [
                ("老師說保證獲利", _框(0, 0, 10, 10)),
                ("先出金再加碼投資，加LINE群組", _框(0, 20, 10, 30)),
            ]
        ),
    )
    route_min = load("a_tbd").get("a_tbd").pack.thresholds.route_min
    image = _圖(tmp_path, "a.png", b"image-a")

    score = _module().can_handle(AnalyzeInput(text="", images=[image]))

    assert score >= route_min
