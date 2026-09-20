"""模型呼叫的界線測試。

「原文不進雲端」要用程式強制，不是寫在註解裡提醒自己 —— 這裡就是那個證明。
"""

from __future__ import annotations

import importlib.util
import json

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
    # 重排序與雲端都還沒鎖 —— 呼叫要報「還沒決議」，不是隨便挑一個跑。
    with pytest.raises(models.ModelNotSelectedError):
        models.rerank("q", ["a"])


def test_地端模型連不上時報的是相依錯不是未鎖定():
    """Ollama 沒開跟模型沒鎖定是兩回事，訊息要講得出下一步。"""
    models._SLM_VERIFIED = ""
    old = models.OLLAMA_HOST
    models.OLLAMA_HOST = "http://127.0.0.1:1"  # 不會有人在聽的埠
    try:
        with pytest.raises(models.ModelDependencyError) as exc:
            models.call_slm("hi")
        assert "ollama" in str(exc.value).lower()
    finally:
        models.OLLAMA_HOST = old
        models._SLM_VERIFIED = ""


def test_digest對不上就擋下來而不是照跑():
    """MODEL_LOCK 的 revision 釘 digest 不是標籤，就是為了擋這件事。

    釘了而不比對等於沒釘 —— 五個人裡有一個人的 qwen2.5:3b 是別的版本，
    分數就不能互比，而且不會有任何徵兆。
    """
    models._SLM_VERIFIED = ""
    real = models._ollama
    name = models.MODEL_LOCK["slm"].name
    models._ollama = lambda path, payload=None, timeout=10.0: {
        "models": [{"name": name, "digest": "deadbeef" * 8}]
    }
    try:
        with pytest.raises(models.ModelDependencyError) as exc:
            models.call_slm("hi")
        assert "digest" in str(exc.value)
    finally:
        models._ollama = real
        models._SLM_VERIFIED = ""


def _ollama_live() -> bool:
    try:
        models._ollama("/api/tags")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_live(), reason="這台沒有在跑 Ollama")
def test_地端模型真的回得出東西而且吃得下格式約束():
    """只有 Ollama 在跑的機器會跑。CI 沒有 Ollama，所以會 skip。"""
    out = models.call_slm("只回四個字：測試成功")
    assert out.strip()

    raw = models.call_slm("把「我用ATM匯款」抽成 JSON，欄位只要 payment。", grammar="json")
    json.loads(raw)  # 不是合法 JSON 就直接炸 —— grammar 沒生效的話會是散文


def test_嵌入沒裝套件時報的是相依錯而不是未鎖定():
    """這兩種錯的下一步完全不同。

    ModelNotSelectedError -> 五個人去決議；ModelDependencyError -> 你自己裝 extra。
    混成同一種會讓人跑去改 MODEL_LOCK，那正好是最不該動的東西。
    """
    if importlib.util.find_spec("torch") is not None:
        pytest.skip("這台裝了 ml 那組套件，走的是真的算向量那條路")
    with pytest.raises(models.ModelDependencyError):
        models.embed(["hi"])
    # 也不能是它的子類 —— 模組的退路靠 except ModelNotSelectedError 接，
    # 變成子類的話「沒裝套件」會被靜靜當成「還沒鎖定」吞掉。
    assert not issubclass(models.ModelDependencyError, models.ModelNotSelectedError)


def test_空清單不必載模型也不必裝套件():
    # 上層拿空語料呼叫時不該炸，而且不該為了回一個空清單去載 2.3 GB 的模型。
    assert models.embed([]) == []


def test_嵌入的執行條件對齊跨平台決議():
    # Mac 的 MPS 預設 fp16，算出來的向量跟 CPU fp32 不一樣、分數不能互比。
    assert models.EMBED_DEVICE == "cpu"
    assert models.EMBED_DTYPE == "float32"
    # bge 系列取 CLS。取成 mean 速度一樣、不會報錯，但向量是垃圾。
    assert models.EMBED_POOLING == "cls"


@pytest.mark.skipif(importlib.util.find_spec("torch") is None, reason="沒裝 ml 那組套件")
def test_真的算得出向量而且已經正規化():
    """只有裝了 extra 的機器會跑。CI 只 uv sync --extra dev，所以會 skip。"""
    v = models.embed(["他叫我去超商買點數然後拍序號給他"])
    assert len(v) == 1
    # 1024 是 bge-m3 的維度，必須跟各模組 Retriever 的 dim 一致，
    # 不一致時 NumpyStore.add() 會擋下來（那是刻意的）。
    assert len(v[0]) == 1024
    # 正規化過，內積才等於餘弦相似度 —— 忘了正規化不會報錯，
    # 只會讓相似度悄悄變成「比較長的文件比較像」。
    assert abs(sum(x * x for x in v[0]) ** 0.5 - 1.0) < 1e-5


def test_溫度鎖死():
    # 五個人要一樣的結果，所以 temperature 不開放外面調
    assert models.TEMPERATURE == 0.0


def test_上下文長度鎖死():
    # 不同的 num_ctx 會在不同的點被截斷，輸出就不能互比。
    # 8192 是實測出來的：4 GB 卡上仍 100% GPU（+150 MiB），16384 就溢出到 CPU。
    assert models.NUM_CTX == 8192


def test_其餘取樣參數也鎖死():
    # 這三個原本沒有測試守著，改了不會有任何紅燈。
    # seed 尤其重要 —— 它跟 temperature 是同一個目的：五個人要一樣的結果。
    assert models.TOP_P == 1.0
    assert models.MAX_TOKENS == 1024
    assert models.SEED == 20260918


def test_模型版本表有四個用途():
    assert set(models.MODEL_LOCK) == {"slm", "embedding", "reranker", "cloud"}
    assert models.all_locked() is False  # S3 還沒做
