"""模型呼叫的界線測試。

「原文不進雲端」要用程式強制，不是寫在註解裡提醒自己 —— 這裡就是那個證明。
"""

from __future__ import annotations

import pytest
from shared import deid, models


def test_雲端呼叫拒收未遮蔽的原文():
    with pytest.raises(models.RawTextLeakError):
        models.call_cloud("我叫陳小明，手機0912345678")  # type: ignore[arg-type]


def test_雲端呼叫拒收自己組出來的假遮蔽物件():
    class 假的:
        text = "看起來像遮過了"
        deid_version = "fake"

    with pytest.raises(models.RawTextLeakError):
        models.call_cloud(假的())  # type: ignore[arg-type]


def test_遮蔽過的文字才進得了雲端的門():
    masked = deid.mask("我被騙了")
    # 過得了界線檢查，但因為 S3 還沒鎖定模型所以停在這裡 —— 這是預期行為
    with pytest.raises(models.ModelNotSelectedError):
        models.call_cloud(masked)


def test_模型未鎖定時報錯而不是偷換一個模型():
    with pytest.raises(models.ModelNotSelectedError):
        models.call_slm("hi")
    with pytest.raises(models.ModelNotSelectedError):
        models.embed(["hi"])


def test_溫度鎖死():
    # 五個人要一樣的結果，所以 temperature 不開放外面調
    assert models.TEMPERATURE == 0.0


def test_模型版本表有四個用途():
    assert set(models.MODEL_LOCK) == {"slm", "embedding", "reranker", "cloud"}
    assert models.all_locked() is False  # S3 還沒做
