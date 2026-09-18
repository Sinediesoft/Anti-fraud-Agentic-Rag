"""模型呼叫 —— 共用三樣之一，不准自己寫（說明書 S5 第 2 點）。

三個函式：呼叫地端小模型、把文字變成向量、呼叫雲端模型。
模型名稱跟隨性程度等參數寫死在這裡，外面改不了 —— 五個人必須用同一組模型，
否則分數不能比。

S3 還沒跑完（模型尚未鎖定），所以 MODEL_LOCK 裡都是 TODO。
在鎖定之前呼叫會丟 ModelNotSelectedError，而不是偷偷換一個模型跑掉。
模組要為這件事寫退路 —— 這正是 S13 要求的三層退路裡的第三層。
"""

from __future__ import annotations

import os
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
    # 地端小模型：候選 Qwen2.5-7B / Llama-3.2-3B / TAIDE-LX-7B，依最低配那台的顯卡記憶體選
    "slm": ModelLock("slm", "TODO-S3", "", "", ""),
    # 嵌入模型：候選 bge-m3 / multilingual-e5-large / text2vec-base-chinese
    # 鎖定後不能換 —— 五個人各建各的向量庫，但嵌入模型必須相同
    "embedding": ModelLock("embedding", "TODO-S3", "", "", ""),
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
    """
    lock = _require("slm")
    # TODO(S3)：接上 Ollama 或 llama.cpp。參數用上面寫死的那組，不開放外面調。
    raise ModelNotSelectedError(f"{lock.name} 的呼叫尚未實作（S3 之後補）")


def embed(texts: list[str]) -> list[list[float]]:
    """把文字變成向量。五個人各建各的向量庫，但都從這裡取向量。"""
    lock = _require("embedding")
    # TODO(S3)：接上 sentence-transformers，模型固定用 lock.name + lock.revision
    raise ModelNotSelectedError(f"{lock.name} 的呼叫尚未實作（S3 之後補）")


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
