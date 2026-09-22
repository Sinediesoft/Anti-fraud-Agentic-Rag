"""M2 截圖理解：讓系統看懂你那個平台的截圖（S11）。

這一步完全自己寫，沒有共用工具可以用。
輸出是「帶版面角色的文字」：哪一種畫面、文字分成哪些塊、對話裡哪句是誰說的。

降級規則：認不出版面時退回「只輸出文字、不標誰說的」，絕不整個當掉。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts import ImageInput


@dataclass
class TextBlock:
    """截圖上的一塊文字。"""

    text: str
    role: str = "unknown"  # title / body / button / price / bubble / field
    speaker: str = "unknown"  # self / other / system —— 對話類才有意義
    bbox: tuple[int, int, int, int] | None = None


@dataclass
class ScreenRead:
    """一張截圖看懂之後的結果。"""

    layout: str = "unknown"  # ad_post / chat / sms / product / checkout / profile
    blocks: list[TextBlock] = field(default_factory=list)
    degraded: bool = False
    reason: str = ""

    @property
    def plain_text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text.strip())


# TODO(S11)：選 OCR（比較 RapidOCR / PaddleOCR / Tesseract 認自己那 20 張的錯字率）
OCR_ENGINE: str | None = None


def read_screenshot(image: ImageInput) -> ScreenRead:
    """把一張截圖變成帶版面角色的文字。

    TODO(S11) 這支要做的三件事：
      1. 判斷這是哪一種畫面
      2. 把文字抓出來並依版面分塊
      3. 對話類的要判斷哪句是對方說的、哪句是自己說的
         （靠氣泡在左邊還右邊、背景色）
    門檻：版面判對率 ≥ 0.85、文字錯誤率 ≤ 0.15。
    """
    if OCR_ENGINE is None:
        # 降級而不是當掉 —— 沒有 OCR 時整條流程仍然要跑得完
        return ScreenRead(
            layout="unknown",
            blocks=[],
            degraded=True,
            reason="OCR 引擎尚未選定（S11）。這次只用打字的內容判讀。",
        )
    raise NotImplementedError("S11 的工作")


def read_all(images: list[ImageInput]) -> list[ScreenRead]:
    return [read_screenshot(img) for img in images]
