"""把檢索到的案例餵給地端模型，生成白話說明（RAG 的 G）。

到這一步為止，檢索出來的相似案例只是被「列出來」—— 使用者拿到的是一份清單，
還得自己看出「所以我現在是什麼處境」。這個模組補的就是那一段：把敘述與檢索
結果一起交給地端小模型，讓它用白話講清楚。

## 三件寫死的規矩

**一、餵進去的是遮蔽過的文字。** 地端模型收原文是允許的（資料不離開這台機器），
但生成結果會顯示在畫面上、會被截圖進報告、也可能被複製貼上到別的地方。模型
把輸入裡的姓名或帳號抄進輸出，那些個資就跟著跑出去了。輸入先遮，輸出就不會有。

**二、只能根據給它的東西講。** prompt 明確要求不要補充沒出現的事實。這件事
擋不掉全部的幻覺，但至少讓「它自己編的」與「案例裡有的」可以被對照出來 ——
每一筆案例都帶著編號，人工要查得回去。

**三、失敗就退回劇本的固定說明。** 跟檢索的三層退路同一個原則：沒裝 Ollama、
模型還沒抓、生成超時，使用者都該拿到一份能用的說明，而不是一個例外。
"""

from __future__ import annotations

from contracts import SimilarCase
from shared import models

# 生成失敗時不重試第二次。理由是延遲：冷啟動 8 秒、暖機後每次 1～2 秒，
# 重試一輪的成本對互動式介面來說太貴，而退路本來就拿得出東西。
MAX_CASES_IN_PROMPT = 3

# 超過這個長度就當成模型跑掉了（要求三句話卻吐出一整篇）。
# 不是硬截斷 —— 截一半的句子比退回固定說明更難看。
MAX_CHARS = 400

# 🔴 第一版的 prompt 寫成「用白話說明發生了什麼」，實測出來的結果是模型把
#    受害者的敘述**原句改寫一遍**。那個輸出看起來很通順，但完全沒有用到檢索
#    到的案例 —— 等於花了十幾秒做一次沒有必要的改寫，跟直接問模型沒兩樣。
#
#    受害者知道自己發生了什麼事。他不知道的是「這代表什麼」「接下來會怎樣」，
#    而那正是相似案例能回答、單一敘述回答不了的。所以規則二與規則三是這份
#    prompt 的重點，不是客套話。
_SYSTEM = """你是協助詐騙受害者的助理。對方已經知道自己的經過，你要告訴他的是這代表什麼。

規則：
1. 先點出這是哪一種手法，再說他現在走到哪一步、這一步的意義是什麼
2. 用相似案例佐證「這是常見的固定套路」以及「接下來通常會發生什麼」
3. 不要複述對方已經講過的經過，也不要照抄案例內容
4. 只能根據下面提供的敘述與案例，不要補充沒出現的事實
5. 繁體中文，三句話以內，不要給行動建議也不要安慰"""


def build_prompt(masked_text: str, similar: list[SimilarCase], stage_name: str = "") -> str:
    """組 prompt。獨立成函式是為了測得到 —— 生成內容沒辦法斷言，但「有沒有
    真的把檢索結果放進去」可以，而那正是 RAG 跟「直接問模型」的差別。"""
    lines = [_SYSTEM, "", f"受害者敘述：{masked_text.strip()}"]
    if stage_name:
        lines.append(f"目前階段：{stage_name}")
    if similar:
        lines.append("")
        lines.append("相似案例：")
        for i, c in enumerate(similar[:MAX_CASES_IN_PROMPT], start=1):
            label = f"（{c.label}）" if c.label else ""
            lines.append(f"{i}. {label}{c.excerpt}")
    lines.append("")
    lines.append("說明：")
    return "\n".join(lines)


def _tidy(text: str) -> str:
    """收掉 3B 模型的重複句。

    實測 qwen2.5:3b 會把同一句話講兩次（「這是常見的固定套路，通常會導致
    資金損失。」出現在第一段與第三段）。這不是 prompt 寫壞，是小模型的常態。

    只丟掉**一模一樣**的句子，不做改寫 —— 判斷兩句話是不是同一個意思需要
    另一個模型，那個成本買不到相稱的品質。
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in " ".join(text.split()).split("。"):  # split() 順便吃掉換行
        s = raw.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return "。".join(out) + "。" if out else ""


def explain(
    masked_text: str, similar: list[SimilarCase], stage_name: str = ""
) -> tuple[str, str | None]:
    """回傳（說明, 退回原因）。退回原因是 None 表示模型真的生成了。

    呼叫端拿到空字串時要沿用劇本裡的固定說明，不要把空的顯示出去。
    """
    if not masked_text.strip():
        return "", "沒有可以說明的內容"

    try:
        out = models.call_slm(build_prompt(masked_text, similar, stage_name)).strip()
    except (models.ModelNotSelectedError, models.ModelDependencyError) as exc:
        return "", f"地端模型不可用：{exc}"
    except Exception as exc:  # 逾時、連線中斷
        return "", f"生成失敗：{type(exc).__name__}: {exc}"

    if not out:
        return "", "模型回了空字串"
    if len(out) > MAX_CHARS:
        return "", f"模型吐了 {len(out)} 字，超過 {MAX_CHARS} 字上限"
    return _tidy(out), None
