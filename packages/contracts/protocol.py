"""四個進入點（說明書 S4 第 1 點）。

外殼只透過這四個函式認識你。實作方式用 Protocol 而不是繼承基底類別：
你的模組不需要 import 任何東西就能符合規格，只要函式簽章對得上。
這樣模組與外殼之間連 import 關係都沒有，合併時撞不到彼此。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schemas import AnalyzeInput, HealthReport, ModuleInfo, Verdict


@runtime_checkable
class ScamModule(Protocol):
    """五個模組都要長成這樣。"""

    def can_handle(self, payload: AnalyzeInput) -> float:
        """這個案子有多像我負責的類型，回 0 到 1。

        要保守。不確定就給低分，讓外殼判定「尚未涵蓋」——
        給錯的建議比說不知道糟得多（S4 注意事項）。
        """
        ...

    def analyze(self, payload: AnalyzeInput) -> Verdict:
        """完整判讀。內部流程完全自由，只要回一份合格的 Verdict。"""
        ...

    def info(self) -> ModuleInfo:
        """我是誰、免費還是付費。"""
        ...

    def health(self) -> HealthReport:
        """我準備好了沒：模型在不在、向量庫建好沒。"""
        ...


# 模組檔案裡要提供一個叫這個名字的工廠函式，外殼靠它拿到實例
ENTRYPOINT_FACTORY = "build_module"
