"""M2 截圖理解：讓系統看懂你那個平台的截圖（S11）。

分兩段，只有第一段是共用的：

  認字    shared.models.ocr()  全隊同一套引擎，同一張圖只認一次（外殼路由前就認過了，
                                這裡拿到的是快取）。回每一行的字、座標、信心分數
  看懂    這個檔案              哪一種畫面、幾行併成一塊、對話裡哪句是誰說的 ——
                                這些跟平台有關，完全自己寫

輸出是「帶版面角色的文字」：哪一種畫面、文字分成哪些塊、對話裡哪句是誰說的。

降級規則：認不出版面時退回「只輸出文字、不標誰說的」，連字都認不出來時退回
「只用打字的內容」，絕不整個當掉。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts import ImageInput
from shared import models


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


def read_screenshot(image: ImageInput) -> ScreenRead:
    """把一張截圖變成帶版面角色的文字。

    TODO(S11) 認字已經由 shared.models.ocr() 做掉，這支剩下兩件事（寫在 _understand）：
      1. 判斷這是哪一種畫面
      2. 依版面把幾行併成一塊；對話類的要判斷哪句是對方說的、哪句是自己說的
         （靠氣泡在左邊還右邊 —— 看 OcrLine.bbox；要看背景色就自己讀 image.path）
    門檻：版面判對率 ≥ 0.85、文字錯誤率 ≤ 0.15。
    """
    try:
        result = models.ocr(image.path)
    except Exception as exc:
        # 降級而不是當掉 —— 這支也被 can_handle() 呼叫，在這裡爆掉會讓整個
        # 模組在路由時拿 0 分，等於一張圖就讓你接不到案子
        return ScreenRead(
            layout="unknown",
            blocks=[],
            degraded=True,
            reason=f"截圖認不出字（{type(exc).__name__}: {exc}）。這次只用打字的內容判讀。",
        )
    return _understand(result)


def _understand(result: models.OcrResult) -> ScreenRead:
    """把認出來的行變成帶版面角色的文字。這是你要寫的部分。

    還沒寫之前走降級：每一行當一塊、不標誰說的。字照樣交得出去，
    判讀可以用截圖上的內容，只是不知道哪句是對方說的。
    """
    blocks = [TextBlock(text=line.text, bbox=line.bbox) for line in result.lines]
    return ScreenRead(
        layout="unknown",
        blocks=blocks,
        degraded=True,
        reason="版面判斷尚未實作（S11）：截圖上的字有用到，但不知道哪句是誰說的。",
    )


def read_all(images: list[ImageInput]) -> list[ScreenRead]:
    return [read_screenshot(img) for img in images]
