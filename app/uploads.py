"""把介面上傳的截圖存成本機檔案（S7 介面的配件）。

檔名用內容的雜湊，不用使用者的原檔名。原本存成「暫存目錄/原檔名」，有兩個問題：

  · 同名的不同截圖會互相覆蓋。手機分享出來的圖常常都叫 image.png，
    多個分頁同時用時，別人同名的截圖也會把你的蓋掉。
  · 同一張圖上傳兩次會變成兩個不同的東西，對話層分不出「這張已經加過了」。

內容一樣就是同一個檔：路徑相同，對話層用路徑就能去重（見 ChatSession.add_images）。

截圖不離開使用者的電腦：只寫到本機的暫存目錄。這支不 import streamlit，所以測得動。
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from contracts import ImageInput

UPLOAD_DIR = Path(tempfile.gettempdir()) / "anti-fraud-uploads"


def save_upload(name: str, data: bytes, *, root: Path | None = None) -> ImageInput:
    """存一張上傳的截圖。同樣的內容只存一份；原檔名留在 filename 給人看。"""
    folder = root or UPLOAD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(name).suffix.lower() or ".png"
    path = folder / f"{hashlib.sha256(data).hexdigest()[:32]}{suffix}"
    if not path.exists():
        path.write_bytes(data)
    return ImageInput(path=str(path), filename=name)
