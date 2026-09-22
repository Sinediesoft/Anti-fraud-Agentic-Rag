"""法規檢索。

對每條切出來的條款做 RAG 檢索，抓最相關的法規全文摘錄／定型化契約應記載
不得記載事項。向量一律走 shared.models.embed()，理由跟 A-E 一樣：
不同的嵌入方式算出來的分數不能互比。

TODO：語料還沒進版控（data/ 被 .gitignore 排除，跟其他模組一致）。
見 README.md「資料來源」一節——v1 先蒐集二三十部相關母法 +
投資顧問/多層次傳銷/求職媒合這幾類應記載不得記載事項，存成本模組
自己的索引（比照 c_tbd/index/ 的做法）。語料進來之前，這裡先留
介面，不假裝有結果可回。
"""

from __future__ import annotations

from pathlib import Path

from contracts import LegalRef

CORPUS_DIR = Path(__file__).resolve().parent / "data"


class CorpusNotBuiltError(RuntimeError):
    """語料/索引還沒建好。見 README.md「資料來源」一節。"""


def retrieve(clause_text: str, *, top_k: int = 5) -> list[LegalRef]:
    """對一條合約條款檢索最相關的法規出處。

    v1 尚未實作：語料庫（母法摘錄 + 應記載不得記載事項）還沒蒐集進
    data/，索引也還沒建。先丟明確的錯誤，不要安靜回空清單假裝比對過。
    """
    raise CorpusNotBuiltError(
        "法規語料庫尚未建置。先依 README.md「資料來源」一節蒐集母法摘錄與"
        "應記載不得記載事項，再用 shared.models.embed() 建索引。"
    )
