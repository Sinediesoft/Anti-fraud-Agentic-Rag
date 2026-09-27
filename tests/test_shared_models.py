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
    # 這條規矩守的是「沒鎖定就報錯」，不是特定哪個模型。
    # slm 與 embedding 在 2026-09-20 鎖定並接線了，所以改用還沒鎖的那兩個舉例：
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


# ── token 記帳（2026-09-27 加）──────────────────────────────────


def test_估算的常數是量出來的():
    """常數換了這條先紅 —— 換模型或 Ollama 改對話樣板都會動到它。"""
    assert models.estimate_slm_tokens("") == 0
    # 2026-09-27 拿 qwen2.5:3b 實測：這句 47 字的敘述吃 62 token
    句 = "我在臉書看到投資廣告，加了對方的LINE，他自稱分析師，帶我在一個App下單，前兩次有出金成功"
    assert abs(models.estimate_slm_tokens(句) - 62) <= 4
    # 嵌入沒有對話樣板那 25 token，所以同一句話估出來一定比 SLM 少
    assert models.estimate_embed_tokens(句) < models.estimate_slm_tokens(句)
    assert models.estimate_embed_tokens("") == 0


def test_數token不會順手載一份tokenizer():
    """單獨載一份 bge-m3 的 fast tokenizer 實測 +384 MB RSS，而整個外殼開機
    才 115 MB。還沒載入時要回 None（「還不知道」），不是 0（「沒有內容」）。"""
    real = models._EMBEDDER
    models._EMBEDDER = ()
    try:
        assert models.count_embed_tokens("我被騙了") is None
    finally:
        models._EMBEDDER = real


def test_截斷旗標兩邊用的證據不一樣():
    # 嵌入：tokenizer 在我們手上，被丟掉幾個是算得出來的確定值
    assert models.Usage(
        "embedding", "bge-m3", prompt_tokens=512, limit=512, longest=512, dropped=771
    ).truncated
    # 生成：Ollama 不會說它截了，而且它的截法是「砍到只剩上限的一半」——
    # 2026-09-27 實測，1,049 token 的 prompt 在 num_ctx=1024 下只吃進 514。
    # 所以「吃進的量 ≥ 上限」永遠不會成立，只能靠估實落差。
    assert models.Usage(
        "slm", "qwen", prompt_tokens=514, limit=1024, estimated_tokens=1049
    ).truncated
    # 估算本身的誤差（一成上下）不可以觸發旗標，否則每次都在喊狼來了。
    # 這組是真實的 C 生成 prompt：490 字，估 411、實際 364。
    assert not models.Usage(
        "slm", "qwen", prompt_tokens=364, limit=8192, estimated_tokens=411
    ).truncated


def test_記帳清得掉也留得住():
    models.reset_usage()
    assert models.usage_log() == []
    assert models.last_usage() is None

    models._record(models.Usage("embedding", "bge-m3", prompt_tokens=21, limit=512, longest=21))
    models._record(models.Usage("slm", "qwen", prompt_tokens=55, output_tokens=14, limit=8192))
    assert len(models.usage_log()) == 2
    assert models.last_usage().purpose == "slm"  # type: ignore[union-attr]
    assert models.last_usage("embedding").prompt_tokens == 21  # type: ignore[union-attr]

    # 回傳的是複本 —— 畫面那一層拿到之後亂改也動不到這裡
    models.usage_log().clear()
    assert len(models.usage_log()) == 2
    models.reset_usage()


def test_記帳不會無上限地長():
    """建索引會把 embed() 呼叫上萬次（81,423 筆 / batch 8）。
    記帳自己不可以變成記憶體問題。"""
    models.reset_usage()
    for _ in range(models.USAGE_LOG_MAX + 30):
        models._record(models.Usage("embedding", "bge-m3", prompt_tokens=1, limit=512))
    assert len(models.usage_log()) == models.USAGE_LOG_MAX
    models.reset_usage()


def test_call_slm把Ollama回傳的token數記下來():
    """以前 call_slm() 只取 "response"，prompt_eval_count 與 eval_count 整包被丟掉。
    畫面上要顯示真實用量，唯一摸得到它們的地方就是這裡。"""
    lock = models.MODEL_LOCK["slm"]
    real = models._ollama

    def fake(path, payload=None, timeout=10.0):
        if path == "/api/tags":
            return {"models": [{"name": lock.name, "digest": lock.revision + "0" * 52}]}
        return {
            "response": "好",
            "prompt_eval_count": 55,
            "eval_count": 14,
            "eval_duration": 236_000_000,  # 奈秒，Ollama 的單位
        }

    models._ollama = fake
    models._SLM_VERIFIED = ""
    models.reset_usage()
    try:
        assert models.call_slm("測試") == "好"
    finally:
        models._ollama = real
        models._SLM_VERIFIED = ""

    usage = models.last_usage("slm")
    assert usage is not None
    assert (usage.prompt_tokens, usage.output_tokens) == (55, 14)
    assert usage.limit == models.NUM_CTX  # 上限是 num_ctx，不是 num_predict
    assert round(usage.tokens_per_s) == 59  # 14 token / 236 ms
    assert usage.estimated_tokens  # 估算值要一起留著，旗標靠它
    assert not usage.truncated
    models.reset_usage()


@pytest.mark.skipif(importlib.util.find_spec("torch") is None, reason="沒裝 ml 那組套件")
def test_嵌入記的是tokenizer真的切出來的數字():
    """只有裝了 extra 的機器會跑。這裡要的是「精確」而不是「大概」——
    512 的截斷是無聲的，估出來的數字擋不住它。"""
    句 = "他叫我去超商買點數然後拍序號給他"
    models.reset_usage()
    models.embed([句])
    usage = models.last_usage("embedding")
    assert usage is not None
    assert usage.items == 1
    assert usage.prompt_tokens == models.count_embed_tokens(句)
    assert usage.limit == models.EMBED_MAX_TOKENS
    assert usage.dropped == 0 and not usage.truncated

    # 超過 512 的那一段：要講得出「被丟掉幾個」，不是只說「有截到」
    長 = 句 * 40
    models.reset_usage()
    models.embed([長])
    usage = models.last_usage("embedding")
    assert usage is not None
    assert usage.longest == models.EMBED_MAX_TOKENS
    assert usage.truncated
    assert usage.dropped == models.count_embed_tokens(長) - models.EMBED_MAX_TOKENS
    models.reset_usage()
