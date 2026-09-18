"""解鎖層（說明書 S7 第 3 點）。

三條硬規則，全部寫成測試：
  一、未解鎖的模組命中時，還是要告訴使用者「我們判斷你的情況屬於 X 類，
      需要解鎖才有完整判讀」—— 不能假裝不知道
  二、風險等級跟 165 導流永遠免費
  三、解鎖邏輯出錯時要往「多給」的方向失敗

規則一是這個產品在倫理上站不站得住的關鍵：一個會因為沒付費就不告訴使用者
「你正在被詐騙」的系統，不該存在。
"""

from __future__ import annotations

from contracts import Plan

from .registry import LoadedModule


class Entitlements:
    """使用者目前的方案。會員系統、金流、訂閱管理都不做（S19 注意事項）。"""

    def __init__(self, *, unlocked: bool = False) -> None:
        self.unlocked = unlocked

    def can_use(self, module: LoadedModule) -> bool:
        if self.unlocked:
            return True
        try:
            return module.pack.plan is Plan.FREE
        except Exception:
            # 規則三：出錯時往「多給」的方向失敗
            return True

    def locked_notice(self, module: LoadedModule) -> str:
        """規則一：命中未解鎖的模組時要說的話。"""
        name = module.pack.name or module.pack.id
        return (
            f"我們判斷你的情況接近「{name}」。"
            f"這個類型需要解鎖才有完整判讀，但下面的風險等級與 165 專線永遠都在。"
        )
