"""模型呼叫 —— 共用三樣之一，不准自己寫（說明書 S5 第 2 點）。

三個函式：呼叫地端小模型、把文字變成向量、呼叫雲端模型。
模型名稱跟隨性程度等參數寫死在這裡，外面改不了 —— 五個人必須用同一組模型，
否則分數不能比。

S3 進行中：地端 SLM 與嵌入模型已鎖定（見 MODEL_LOCK），重排序／雲端仍是 TODO。
未鎖定的那幾個被呼叫時會丟 ModelNotSelectedError，而不是偷偷換一個模型跑掉。
模組要為這件事寫退路 —— 這正是 S13 要求的三層退路裡的第三層。

鎖定與「接得上」是兩件事：embed() 已經真的接上 bge-m3，call_slm() 與其餘兩個
還只有鎖定表。所以模組的退路現在仍然必要，只是觸發的原因會從「還沒鎖」
變成「這台沒裝 ml 那組套件」（ModelDependencyError）。
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass

from contracts import MaskedText

from .deid import is_masked


class ModelNotSelectedError(RuntimeError):
    """S3 還沒鎖定模型就呼叫。訊息要講清楚該去做哪一步。"""


class RawTextLeakError(ValueError):
    """有人想把沒遮過的原文送進雲端。這是硬界線，不是警告。"""


class ModelDependencyError(RuntimeError):
    """模型鎖定了、程式也接上了，但這台機器少裝跑它需要的套件。

    跟 ModelNotSelectedError 分開是刻意的：那個的下一步是「五個人去決議」，
    這個的下一步是「你自己裝 extra」。混成同一種例外會讓人跑去改 MODEL_LOCK，
    那正好是最不該動的東西。
    """


@dataclass(frozen=True)
class ModelLock:
    """模型版本表（說明書 S3 第 5 點）。

    壓縮程度也算版本 —— 五個人要用同一份檔案，不是同一個名字。
    這張表鎖定後貼在共用文件最上面，而且要跟這裡的內容一致。
    """

    purpose: str
    name: str
    revision: str
    quantization: str
    locked_on: str

    @property
    def is_locked(self) -> bool:
        return bool(self.name) and not self.name.startswith("TODO")


# TODO(S3)：五人決議後填滿這張表，並同步更新 docs/model-lock.md
MODEL_LOCK: dict[str, ModelLock] = {
    # 地端小模型：2026-09-20 鎖定。說明書清單內唯一合 4 GB 天花板的是 Llama-3.2-3B，
    # 但它官方不支援中文，所以偏離一格用同家族的 Qwen2.5-3B（這個偏離要寫進報告）。
    # revision 填 Ollama 的 digest 不是標籤 —— 標籤會被上游重新指向，digest 不會。
    # B(GTX1650/4G)、C(GTX1650/4G)、E(5070Ti/16G)、A(M5/Metal) 四台實測一致。
    "slm": ModelLock("slm", "qwen2.5:3b", "357c53fb659c", "Q4_K_M", "2026-09-20"),
    # 嵌入模型：2026-09-20 鎖定。C 實測兩個候選都過得了 S12 的 1 秒門檻，
    # 選 bge-m3 是因為延遲付得起就該把預算花在品質上 —— 它 p50 只吃掉 13%
    # 預算（p95 20%），剩下的留給檢索與生成仍然寬裕，而且支援繁中。
    # revision 填 HuggingFace 的 commit SHA，不是分支名 —— 理由同 slm。
    # dtype 跟著本專案的跨平台決議走 fp32：MPS 與 CUDA 的低位數值差異會讓
    # 五個人的分數不能互比，這比那點速度重要。
    #
    # ⚠ 代價：建索引 3000 筆要 43 分鐘（text2vec 只要 19 分鐘），而且五個人
    #   各建各的。所以切塊策略要先定案再建正式索引，不要邊建邊改。
    # 退路 text2vec-base-chinese（102M，p95 45ms，只吃 4% 預算）：
    #   sha 183bb99aa7af74355fb58d16edf8c13ae7c5433e
    #   真的要換的代價是五個人的向量庫全部重建 —— 鎖定後不能換講的就是這件事。
    "embedding": ModelLock(
        "embedding",
        "BAAI/bge-m3",
        "5617a9f61b028005a4858fdac845db406aefb181",
        "fp32",
        "2026-09-20",
    ),
    # 重排序模型：說明書建議 bge-reranker-v2-m3
    "reranker": ModelLock("reranker", "TODO-S3", "", "", ""),
    # 雲端模型：選一家、一個型號、一個版本
    "cloud": ModelLock("cloud", "TODO-S3", "", "", ""),
}

# 寫死的參數。要一樣的結果，所以 temperature 鎖 0
TEMPERATURE = 0.0
TOP_P = 1.0
MAX_TOKENS = 1024
SEED = 20260918

# 上下文長度也要鎖，理由不是「預設不夠用」—— 實測 Ollama 預設的 4096 其實
# 裝得下目前的 S13 prompt（12 則示範題 538 token，加檢索 5 筆也才 2079）。
# 真正的理由有三個：
#
#   1. 餘裕只有兩倍。2079/4096 已經用掉一半，示範題變長或檢索筆數變多就會
#      吃掉它，而超出時 Ollama 不報錯，只是安靜從前面截掉。
#   2. 加大幾乎不用錢。4 GB 卡上 4096->8192 只多 150 MiB（B 實測 2127->2277），
#      仍是 100% GPU，還剩約 1.8 GB。GQA 讓 KV 快取很小。
#   3. 再往上才是懸崖。16384 會把部分層丟回 CPU，無聲掉速三成（C、E 實測）。
#
# 呼叫 SLM 時一定要明確帶上，不要靠預設值 —— 五個人用不同的值會在不同的點
# 被截斷，輸出就不能互比。截斷的可觀測訊號見 tools/bench/slm_truncation.py。
#
# ⚠ 越線的代價不是「切掉超出的那幾個」：2026-09-27 實測，同一個 1,049 token 的
#   prompt 在 num_ctx=1024 下只吃進 514、512 下只吃進 258、256 下只吃進 130 ——
#   一超過就砍到剩上限的一半。所以 8192 的意思是「8192 以內安全」，不是
#   「有 8192 可以花」，而且它不會報錯。偵測方式見 Usage.truncated。
NUM_CTX = 8192

# ── 嵌入模型的執行條件 ──────────────────────────────────────────────
#
# 這幾個值跟 tools/bench/embed_latency.py 量出 docs/model-lock.md 那組延遲數字
# 時用的完全一致。改了就不能再拿那些數字當依據。
#
# device 與 dtype 是「跨平台決議」釘死的，不是效能取捨：Mac 的 MPS 預設 fp16，
# 同一個模型算出來的向量跟 CPU fp32 不一樣，而向量不一樣就代表分數不能互比。
# 寧可慢，也不要五個人的分數各自為政。
EMBED_DEVICE = "cpu"
EMBED_DTYPE = "float32"
EMBED_MAX_TOKENS = 512  # 超過就截斷。bge-m3 撐得住 8192，但實測的是 512
EMBED_BATCH = 8

# 取池化方式。bge 系列取 CLS，text2vec 系列取 mean —— 取錯速度一樣，但向量
# 會是垃圾，而且不會報錯。退路 text2vec-base-chinese 真的被換上來時，
# 這一行要跟著 MODEL_LOCK["embedding"] 一起改。
EMBED_POOLING = "cls"

# 估算用的字/token 比例。實測見 estimate_embed_tokens()。精確值請用
# count_embed_tokens() —— tokenizer 已經載入時它才是對的那一個。
EMBED_CHARS_PER_TOKEN = 1.27

# ── 地端 SLM 的連線設定 ────────────────────────────────────────────
#
# 走 Ollama 的 HTTP API，用標準函式庫的 urllib —— 不為了這件事多一個相依。
# 位址可以用環境變數改（有人把 Ollama 裝在教室電腦上，見環境對齊追蹤表的
# D 那欄），但取樣參數不行，那是五個人要一致的東西。
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
# timeout 也走環境變數：位址既然能指到別台機器，延遲就不會跟本機一樣。
# 預設 180 是本機實測 —— 冷啟動 9.2 秒，長輸出會更久；寧可等也不要半路砍掉。
SLM_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT", "180"))

# ── token 記帳 ──────────────────────────────────────────────────────
#
# Ollama 的回傳裡本來就帶著 prompt_eval_count 與 eval_count，而 call_slm()
# 以前只取 "response" 就把整包丟掉了。外殼看不到 HTTP 回傳、模組拿到的是一個
# 字串 —— 真實的 token 數只有這一層摸得到。
#
# 這一段只記帳：取樣參數、num_ctx、截斷行為一個都沒動。


# 一個 token 大概幾個字（實測見 estimate_slm_tokens）。
SLM_CHARS_PER_TOKEN = 1.27
SLM_TEMPLATE_TOKENS = 25


@dataclass(frozen=True)
class Usage:
    """一次模型呼叫吃掉多少 token。

    purpose 跟 MODEL_LOCK 的鍵一致（slm / embedding），畫面才分得出這筆是
    檢索還是生成。limit 是這次呼叫真正的天花板。
    """

    purpose: str
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    limit: int = 0
    items: int = 1  # 這次餵了幾段文字。嵌入建索引是一批 8 段，生成永遠是 1
    longest: int = 0  # 最長那一段幾個 token —— 上限是「每段」的，所以看這個
    dropped: int = 0  # 確定被丟掉幾個 token（嵌入算得出來，生成算不出來）
    estimated_tokens: int = 0  # 送出前估的值。留著是為了抓無聲截斷
    gen_ms: float = 0.0

    @property
    def tokens_per_s(self) -> float:
        return 1000.0 * self.output_tokens / self.gen_ms if self.gen_ms else 0.0

    @property
    def load_pct(self) -> float:
        """最長那一段用掉上限的幾成。"""
        return 100.0 * (self.longest or self.prompt_tokens) / self.limit if self.limit else 0.0

    @property
    def truncated(self) -> bool:
        """有東西被無聲截掉了。兩邊拿得到的證據不一樣：

        **嵌入**：tokenizer 在我們手上，截斷前後的長度都數得出來，dropped 是
        確定的數字。

        **生成**：Ollama 不會說它截了，而且它的截法不是「切掉超出的部分」——
        2026-09-27 實測，同一個 1,049 token 的 prompt 在 num_ctx=1024 下只吃進
        514、512 下 258、256 下 130，全部剛好是上限的 50.x%。所以
        「吃進的量 ≥ 上限」永遠不會成立，拿它當旗標等於沒有旗標。能用的證據是
        估算值與實際值的落差：估算誤差在一成上下，而截斷會讓實際值掉到一半，
        兩者差得夠遠，抓 80% 這條線不會誤判。
        """
        if self.dropped > 0:
            return True
        if self.estimated_tokens and self.prompt_tokens:
            return self.prompt_tokens < self.estimated_tokens * 0.8
        return False


# 記幾筆就好。建索引會把 embed() 呼叫上萬次，記帳不能跟著無上限地長。
USAGE_LOG_MAX = 64

_USAGE: list[Usage] = []

# 記帳要上鎖，理由跟 _EMBEDDER_LOCK 同一個：外殼會用背景執行緒預熱嵌入模型
# （app/ui.py），那條執行緒也會走到記帳。
_USAGE_LOCK = threading.Lock()


def _record(usage: Usage) -> None:
    with _USAGE_LOCK:
        _USAGE.append(usage)
        if len(_USAGE) > USAGE_LOG_MAX:
            del _USAGE[:-USAGE_LOG_MAX]


def reset_usage() -> None:
    """把帳清掉。呼叫端要在「這一次判讀」開始前呼叫，畫面才不會算進上一次。"""
    with _USAGE_LOCK:
        _USAGE.clear()


def usage_log() -> list[Usage]:
    """這一次記到的每一筆。回傳複本 —— 畫面那一層改不到內部狀態。"""
    with _USAGE_LOCK:
        return list(_USAGE)


def last_usage(purpose: str | None = None) -> Usage | None:
    """最後一筆。purpose 帶了就只找那一種（slm / embedding）。"""
    with _USAGE_LOCK:
        for usage in reversed(_USAGE):
            if purpose is None or usage.purpose == purpose:
                return usage
    return None


def estimate_slm_tokens(text: str) -> int:
    """這段文字送進地端模型大概是幾個 token。**是估的。**

    精確值要等呼叫完才有（Ollama 的 prompt_eval_count），而畫面需要在使用者
    還在打字的時候就講出一個數字。

    常數的來源（2026-09-27 在 A 的機器上拿 qwen2.5:3b 實測）：真實繁中敘述
    47 字→62、86 字→93、54 字→66 token，扣掉樣板之後一律落在 1.27～1.32
    字/token；樣板開銷固定約 25 token（1 個字的 prompt 也回 30，空字串回 0）。
    混到英數與標點會高估：真實的 C 生成 prompt 490 字，估 411、實際 364。

    為什麼不先打一次 Ollama 拿精確值：可以，num_predict=1 暖機後只要 25 ms
    （num_predict=0 沒有用，實測它照樣自由生成、跑了 4.7 秒）。但那條路要
    qwen 一直待在記憶體裡（2.4 GB），跟 docs/記憶體評估.md 的結論衝突，而且
    Ollama 閒置五分鐘就卸載，卸載後第一次是 788 ms～9.2 秒。
    """
    if not text:
        return 0
    return SLM_TEMPLATE_TOKENS + round(len(text) / SLM_CHARS_PER_TOKEN)


def estimate_embed_tokens(text: str) -> int:
    """bge-m3 的 token 數估算值。tokenizer 還沒載入時的替代品。

    比例跟 SLM 那邊巧合地接近（2026-09-27 實測 bge-m3：230 字→183 token、
    920 字→723 token），但**不加樣板開銷** —— 那 25 token 是 Ollama 套對話
    樣板加上去的，嵌入沒有這回事。
    """
    if not text:
        return 0
    return round(len(text) / EMBED_CHARS_PER_TOKEN)


def count_embed_tokens(text: str) -> int | None:
    """這段文字在 bge-m3 眼裡是幾個 token。**精確值**，不是估的。

    tokenizer 還沒載入時回 None ——「還不知道」跟「0 個」是兩件事，畫面要分得開。

    刻意不為了數 token 去載 tokenizer：單獨載一份 bge-m3 的 fast tokenizer
    實測 +384 MB RSS、149 ms（2026-09-27，純 tokenizers 不含 torch），而整個
    外殼開機才 115 MB。開機預熱（app/ui.py）跑完之後它本來就在記憶體裡，
    那時候數一次只要 0.1～0.3 ms，等於免費。
    """
    if not text or not _EMBEDDER:
        return None
    tok = _EMBEDDER[1]
    return len(tok(text)["input_ids"])


def token_gauge(text: str) -> tuple[int, bool]:
    """（token 數, 是不是精確值）。畫面右側那一格就靠這個。

    決策留在這一層而不是介面層：tokenizer 載入與否是模型的事，介面只負責畫。
    而且 app/ui.py 匯入時會直接跑起整個畫面（streamlit run 的跑法），測不動 ——
    邏輯放在這裡才測得到。
    """
    exact = count_embed_tokens(text)
    if exact is not None:
        return exact, True
    return estimate_embed_tokens(text), False


def _require(purpose: str) -> ModelLock:
    lock = MODEL_LOCK[purpose]
    if not lock.is_locked:
        raise ModelNotSelectedError(
            f"{purpose} 模型尚未鎖定。這是 S3 的工作：五人決議後填 shared/models.py "
            f"的 MODEL_LOCK 與 docs/model-lock.md。在那之前請走模組自己的退路。"
        )
    return lock


def _ollama(path: str, payload: dict | None = None, timeout: float = 10.0) -> dict:
    """打 Ollama 的 HTTP API。連不上要講清楚下一步，不要丟原始的連線錯誤。"""
    url = f"{OLLAMA_HOST.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(  # noqa: S310 —— 位址是本機常數，不是使用者輸入
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ModelDependencyError(
            f"Ollama 回了 HTTP {exc.code}。模型沒抓下來的話跑：ollama pull {MODEL_LOCK['slm'].name}"
        ) from exc
    except OSError as exc:
        raise ModelDependencyError(
            f"連不上 Ollama（{OLLAMA_HOST}）。先確認它在跑：ollama serve。"
            f"裝在別台機器的話用環境變數 OLLAMA_HOST 指過去。"
        ) from exc


_SLM_VERIFIED = ""


def _verify_slm(lock: ModelLock) -> None:
    """確認跑的真的是鎖定的那一份，不是同名的另一版。

    MODEL_LOCK 的 revision 釘的是 digest 不是標籤，理由就在這裡：標籤會被
    上游重新指向，digest 不會。但釘了而不比對等於沒釘 —— 五個人裡只要有
    一個人的 qwen2.5:3b 是別的版本，分數就不能互比，而且不會有任何徵兆。
    """
    global _SLM_VERIFIED
    if _SLM_VERIFIED == lock.revision:
        return
    tags = _ollama("/api/tags").get("models", [])
    found = next((m for m in tags if m.get("name") == lock.name), None)
    if found is None:
        raise ModelDependencyError(f"Ollama 裡沒有 {lock.name}。跑：ollama pull {lock.name}")
    digest = str(found.get("digest", ""))
    if not digest.startswith(lock.revision):
        raise ModelDependencyError(
            f"{lock.name} 的 digest 對不上鎖定表：這台是 {digest[:12]}，"
            f"鎖定的是 {lock.revision}。重抓一次（ollama pull）或回頭確認 "
            f"MODEL_LOCK —— 版本不同的模型算出來的分數不能互比。"
        )
    _SLM_VERIFIED = lock.revision


def call_slm(prompt: str, *, grammar: str | None = None) -> str:
    """呼叫地端小模型。原文可以進來 —— 它在使用者自己的電腦上跑，資料不離開。

    grammar 是格式約束：強制模型只能吐出符合格式的答案（S13 第 4 點的第一層
    退路）。Ollama 收 "json" 或一份 JSON Schema，直接轉給它的 format 欄位。

    取樣參數一律用本模組寫死的那組，不開放呼叫端調 —— 尤其 num_ctx：
    五個人用不同的值會在不同的點被截斷，而超出時 Ollama 不報錯，只是安靜
    從前面截掉。
    """
    lock = _require("slm")
    _verify_slm(lock)
    body = {
        "model": lock.name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "seed": SEED,
            "num_predict": MAX_TOKENS,
            "num_ctx": NUM_CTX,
        },
    }
    if grammar:
        body["format"] = grammar
    data = _ollama("/api/generate", body, timeout=SLM_TIMEOUT_S)
    _record(
        Usage(
            purpose="slm",
            model=lock.name,
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            limit=NUM_CTX,
            longest=int(data.get("prompt_eval_count") or 0),
            estimated_tokens=estimate_slm_tokens(prompt),
            gen_ms=float(data.get("eval_duration") or 0) / 1e6,
        )
    )
    return data.get("response", "")


def _import_ml():
    """torch 與 transformers 走延遲 import。

    跟 m1_corpus 讀 parquet 同一個做法，理由在本專案更強：torch 在 macOS ARM
    與 Windows CUDA 上是不同的 wheel，列成必裝會讓某些人 uv sync 直接失敗。
    所以它在 pyproject 的 ml 這組 extra 裡，預設不裝，import 也延遲到真的要
    算向量的那一刻 —— 沒裝的人照樣 import 得了這個模組，只是走不到第一層。
    """
    try:
        import torch
        import torch.nn.functional as F
        from transformers import AutoModel, AutoTokenizer
    except ImportError as exc:
        raise ModelDependencyError(
            "嵌入模型要用 torch 與 transformers，這台沒裝。裝法：uv sync --extra ml "
            "（torch 的 wheel 依平台而異，見 pyproject.toml 的註解）。"
            "只是想量延遲、不想動專案環境的話，用 tools/bench/ 的隔離環境跑。"
        ) from exc
    return torch, F, AutoModel, AutoTokenizer


# 同一個 process 只載一次。實測冷啟動 5.3 秒，每次查詢重載會讓 S12 的 1 秒
# 門檻直接沒救。鍵帶上 revision：模型換版時快取要跟著失效。
_EMBEDDER: tuple = ()


# 載入要上鎖：外殼會在開機時用背景執行緒預熱（app/ui.py），使用者手速夠快
# 的話第一次查詢會跟預熱撞在一起 —— 沒有鎖就是兩條執行緒各載一份 2.2GB 的
# bge-m3，16GB 的機器會很難看。單執行緒的呼叫者完全感覺不到這把鎖。
_EMBEDDER_LOCK = threading.Lock()


def _load_embedder(lock: ModelLock):
    global _EMBEDDER
    key = f"{lock.name}@{lock.revision}"
    if _EMBEDDER and _EMBEDDER[0] == key:
        return _EMBEDDER[1], _EMBEDDER[2]

    with _EMBEDDER_LOCK:
        # 拿到鎖之後要再查一次：排隊的時候前面那個人可能已經載完了。
        if _EMBEDDER and _EMBEDDER[0] == key:
            return _EMBEDDER[1], _EMBEDDER[2]
        return _load_embedder_locked(lock, key)


def _load_embedder_locked(lock: ModelLock, key: str):
    global _EMBEDDER
    torch, _F, AutoModel, AutoTokenizer = _import_ml()
    # revision 一定要帶 —— 不帶就是跟著上游的 main 跑，那會讓 MODEL_LOCK
    # 釘住的那個 commit 形同虛設，而且不會有任何徵兆。
    tok = AutoTokenizer.from_pretrained(lock.name, revision=lock.revision)
    model = AutoModel.from_pretrained(
        lock.name, revision=lock.revision, dtype=getattr(torch, EMBED_DTYPE)
    )
    model.to(EMBED_DEVICE)
    model.eval()
    _EMBEDDER = (key, tok, model)
    return tok, model


def _pool(hidden, mask, F):
    """把 token 向量收成一條句向量，並正規化成單位長度。"""
    if EMBED_POOLING == "cls":
        return F.normalize(hidden[:, 0], p=2, dim=1)
    m = mask.unsqueeze(-1).expand(hidden.size()).float()
    return F.normalize((hidden * m).sum(1) / m.sum(1).clamp(min=1e-9), p=2, dim=1)


def embed(texts: list[str]) -> list[list[float]]:
    """把文字變成向量。五個人各建各的向量庫，但都從這裡取向量。

    回傳的向量**已經 L2 正規化**，所以兩條向量的內積就是餘弦相似度，上層可以
    直接用矩陣乘法算分數。這件事寫在這裡而不是留給呼叫端，是因為忘了正規化
    不會報錯，只會讓相似度悄悄變成「長度較長的文件比較像」。

    成本提醒（實測）：查詢一句話 p50 127 ms，但建索引是 1.17 筆/秒 ——
    3000 筆要 43 分鐘，而且五個人各建各的。切塊策略先定案再建正式索引。
    """
    lock = _require("embedding")
    if not texts:
        return []

    torch, F, _AutoModel, _AutoTokenizer = _import_ml()
    tok, model = _load_embedder(lock)

    out: list[list[float]] = []
    counts: list[int] = []
    dropped = 0
    with torch.inference_mode():
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i : i + EMBED_BATCH]
            enc = tok(
                batch,
                padding=True,
                truncation=True,
                max_length=EMBED_MAX_TOKENS,
                return_tensors="pt",
            )
            # 補齊之後每一列都一樣長，所以要數 attention_mask 的和，不是張量
            # 的形狀 —— 數形狀會把 padding 當成真的內容。
            lengths = [int(n) for n in enc["attention_mask"].sum(dim=1).tolist()]
            counts.extend(lengths)
            # 頂到上限的那幾段，再不截斷地切一次，才講得出被丟掉幾個。
            # 只在頂到上限時才做，所以建索引不會多花錢。
            for text, n in zip(batch, lengths, strict=True):
                if n >= EMBED_MAX_TOKENS:
                    dropped += max(0, len(tok(text)["input_ids"]) - n)
            out.extend(_pool(model(**enc).last_hidden_state, enc["attention_mask"], F).tolist())
    _record(
        Usage(
            purpose="embedding",
            model=lock.name,
            prompt_tokens=sum(counts),
            limit=EMBED_MAX_TOKENS,
            items=len(counts),
            longest=max(counts, default=0),
            dropped=dropped,
        )
    )
    return out


def rerank(query: str, candidates: list[str]) -> list[float]:
    """重排序：比較慢但比較準，把最相關的往上提。"""
    lock = _require("reranker")
    # TODO(S3)：接上 bge-reranker
    raise ModelNotSelectedError(f"{lock.name} 的呼叫尚未實作（S3 之後補）")


def call_cloud(prompt: MaskedText, *, system: str | None = None) -> str:
    """呼叫雲端模型。只收遮蔽過的文字。

    這是「原文不進雲端」那條界線的實作處（說明書 S5）。
    檢查用程式強制，不是寫在註解裡提醒自己 —— 型別不對就報錯，沒有例外。
    """
    if not is_masked(prompt):
        raise RawTextLeakError(
            "call_cloud 只收 shared.deid.mask() 產生的 MaskedText。"
            "收到未遮蔽的原文表示有人繞過去識別化 —— 這會讓報告裡的隱私承諾不成立。"
        )
    lock = _require("cloud")
    if not os.getenv("CLOUD_LLM_API_KEY"):
        raise ModelNotSelectedError("CLOUD_LLM_API_KEY 未設定。金鑰用環境變數，不要進儲存庫。")
    # TODO(S3)：接上雲端 API，並記得設用量上限
    raise ModelNotSelectedError(f"{lock.name} 的呼叫尚未實作（S3 之後補）")


def lock_table() -> list[dict[str, str]]:
    """把模型版本表吐成一張表，寫進實驗紀錄與報告用。"""
    return [
        {
            "用途": lock.purpose,
            "模型名": lock.name,
            "版本編號": lock.revision or "-",
            "壓縮格式": lock.quantization or "-",
            "鎖定日期": lock.locked_on or "尚未鎖定",
        }
        for lock in MODEL_LOCK.values()
    ]


def all_locked() -> bool:
    """S3 做完了沒。外殼的 health 與 selfcheck 會問這個。"""
    return all(lock.is_locked for lock in MODEL_LOCK.values())
