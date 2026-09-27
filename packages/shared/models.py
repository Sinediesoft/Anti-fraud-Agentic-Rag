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

# ── 地端 SLM 的連線設定 ────────────────────────────────────────────
#
# 走 Ollama 的 HTTP API，用標準函式庫的 urllib —— 不為了這件事多一個相依。
# 位址可以用環境變數改（有人把 Ollama 裝在教室電腦上，見環境對齊追蹤表的
# D 那欄），但取樣參數不行，那是五個人要一致的東西。
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
# timeout 也走環境變數：位址既然能指到別台機器，延遲就不會跟本機一樣。
# 預設 180 是本機實測 —— 冷啟動 9.2 秒，長輸出會更久；寧可等也不要半路砍掉。
SLM_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT", "180"))


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
    return _ollama("/api/generate", body, timeout=SLM_TIMEOUT_S).get("response", "")


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
    with torch.inference_mode():
        for i in range(0, len(texts), EMBED_BATCH):
            enc = tok(
                texts[i : i + EMBED_BATCH],
                padding=True,
                truncation=True,
                max_length=EMBED_MAX_TOKENS,
                return_tensors="pt",
            )
            out.extend(_pool(model(**enc).last_hidden_state, enc["attention_mask"], F).tolist())
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
