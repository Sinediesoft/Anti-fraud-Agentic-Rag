"""模型呼叫 —— 共用三樣之一，不准自己寫（說明書 S5 第 2 點）。

三個函式：呼叫地端小模型、把文字變成向量、呼叫雲端模型。
模型名稱跟隨性程度等參數寫死在這裡，外面改不了 —— 五個人必須用同一組模型，
否則分數不能比。

S3 進行中：地端 SLM 與嵌入模型已鎖定（見 MODEL_LOCK），重排序／雲端仍是 TODO。
未鎖定的那幾個被呼叫時會丟 ModelNotSelectedError，而不是偷偷換一個模型跑掉。
模組要為這件事寫退路 —— 這正是 S13 要求的三層退路裡的第三層。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from contracts import MaskedText

from .deid import is_masked


class ModelNotSelectedError(RuntimeError):
    """S3 還沒鎖定模型就呼叫。訊息要講清楚該去做哪一步。"""


class RawTextLeakError(ValueError):
    """有人想把沒遮過的原文送進雲端。這是硬界線，不是警告。"""


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

# Ollama 的位置。預設是本機 —— 地端模型的重點就是資料不離開這台電腦。
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))


def _require(purpose: str) -> ModelLock:
    lock = MODEL_LOCK[purpose]
    if not lock.is_locked:
        raise ModelNotSelectedError(
            f"{purpose} 模型尚未鎖定。這是 S3 的工作：五人決議後填 shared/models.py "
            f"的 MODEL_LOCK 與 docs/model-lock.md。在那之前請走模組自己的退路。"
        )
    return lock


def call_slm(prompt: str, *, grammar: str | None = None) -> str:
    """呼叫地端小模型。原文可以進來 —— 它在使用者自己的電腦上跑，資料不離開。

    grammar 是格式約束：強制模型只能吐出符合格式的答案（S13 第 4 點的第一層退路）。
    Ollama 用 format 參數收 JSON schema。

    走 Ollama 的 HTTP API，只用標準函式庫 —— 不引入新相依。
    num_ctx 一定要明確帶上：Ollama 預設只給 4096，而超出時它不報錯，
    只會安靜從前面截掉，最先被丟掉的就是系統指示。
    """
    lock = _require("slm")
    payload: dict = {
        "model": lock.name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "seed": SEED,
            "num_ctx": NUM_CTX,
            "num_predict": MAX_TOKENS,
        },
    }
    if grammar:
        payload["format"] = json.loads(grammar) if grammar.strip().startswith("{") else grammar

    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as r:
            body = json.loads(r.read())
    except urllib.error.URLError as e:
        raise ModelNotSelectedError(
            f"連不上 Ollama（{OLLAMA_HOST}）：{e.reason}。"
            f"請先啟動 `ollama serve`，並確認 `ollama list` 裡有 {lock.name}。"
        ) from e
    return body.get("response", "").strip()


_EMBEDDER = None


def embed(texts: list[str]) -> list[list[float]]:
    """把文字變成向量。五個人各建各的向量庫，但都從這裡取向量。

    模型與 revision 都鎖死：revision 不釘的話，上游更新後大家的向量就不同源，
    而向量庫是各自建的 —— 不會有人發現，只會發現分數對不起來。

    device 與 dtype 跟著跨平台決議走 cpu / float32。MPS 與 CUDA 的低位數值
    差異會讓五個人的分數不能互比，這比那點速度重要。

    sentence-transformers 與 torch 刻意不在專案相依裡（requirements.txt 寫明
    torch 是硬體相依、故意不鎖版本，而 pyproject.toml 與 uv.lock 是凍結的
    共管路徑）。所以這裡延遲匯入，沒裝的人會拿到裝法，不是 ImportError。
    """
    global _EMBEDDER
    lock = _require("embedding")
    if _EMBEDDER is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ModelNotSelectedError(
                "缺少 sentence-transformers。它刻意不在專案相依裡（torch 是硬體相依），"
                "請用隔離環境跑：\n"
                "    uv run --no-project --python 3.11 --with sentence-transformers "
                "python 你的程式.py\n"
                "或只在本機裝：uv pip install sentence-transformers"
            ) from e
        _EMBEDDER = SentenceTransformer(lock.name, revision=lock.revision or None, device="cpu")
    return [v.tolist() for v in _EMBEDDER.encode(texts, convert_to_numpy=True)]


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
