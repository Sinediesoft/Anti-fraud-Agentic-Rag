"""越界檢查的測試。這支機器人是「三樣東西不准自己寫」唯一的執法者。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.check_boundaries import scan_file, scan_module

MODULES = Path(__file__).resolve().parent.parent / "packages" / "modules"


def test_現有模組都沒有越界():
    for module_dir in MODULES.iterdir():
        if module_dir.is_dir():
            assert scan_module(module_dir) == [], module_dir.name


def test_擋下自己呼叫模型(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("import torch\n", encoding="utf-8")
    assert any("shared.models" in v.message for v in scan_file(f, "a_tbd"))


@pytest.mark.parametrize(
    "line", ["import rapidocr\n", "from paddleocr import PaddleOCR\n", "import pytesseract\n"]
)
def test_擋下自己跑OCR引擎(tmp_path, line):
    # rapidocr 底層就是 onnxruntime。只擋 onnxruntime 的話，這條會過
    f = tmp_path / "bad.py"
    f.write_text(line, encoding="utf-8")
    assert any("shared.models" in v.message for v in scan_file(f, "a_tbd"))


def test_擋下自己連網(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("import httpx\n", encoding="utf-8")
    assert any("不連網" in v.message for v in scan_file(f, "a_tbd"))


def test_擋下自己算分數(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("from sklearn.metrics import f1_score\n", encoding="utf-8")
    assert any("shared.eval" in v.message for v in scan_file(f, "a_tbd"))


def test_擋下_import_別人的模組(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("from modules.b_tbd import module\n", encoding="utf-8")
    assert any("別人的模組" in v.message for v in scan_file(f, "a_tbd"))


def test_import_自己的模組沒問題(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("from modules.a_tbd import m1_corpus\n", encoding="utf-8")
    assert scan_file(f, "a_tbd") == []


def test_擋下自己重寫去識別化(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def mask(text):\n    return text\n", encoding="utf-8")
    assert any("shared.deid" in v.message for v in scan_file(f, "a_tbd"))


def test_用共用工具沒問題(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text(
        "from shared import deid, models\nfrom contracts import Verdict\n", encoding="utf-8"
    )
    assert scan_file(f, "a_tbd") == []
