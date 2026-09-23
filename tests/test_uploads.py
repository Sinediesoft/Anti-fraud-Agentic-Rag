"""介面上傳的截圖怎麼存（app/uploads.py）。"""

from __future__ import annotations

from pathlib import Path

from app.uploads import save_upload


def test_同一份內容不管叫什麼名字都存成同一個檔(tmp_path):
    # 路徑一樣，對話層才能靠路徑認出「這張已經加過了」
    a = save_upload("IMG_0001.PNG", b"same-bytes", root=tmp_path)
    b = save_upload("另存一份.png", b"same-bytes", root=tmp_path)

    assert a.path == b.path
    assert Path(a.path).read_bytes() == b"same-bytes"
    assert b.filename == "另存一份.png"  # 原檔名跟著每一次上傳，降級訊息才講得出是哪張


def test_同檔名的不同截圖不會互相覆蓋(tmp_path):
    # 原本存成「暫存目錄/原檔名」：第二張 image.png 會把第一張蓋掉，
    # 多個分頁同時用時，別人同名的截圖也會蓋掉你的
    first = save_upload("image.png", b"first", root=tmp_path)
    second = save_upload("image.png", b"second", root=tmp_path)

    assert first.path != second.path
    assert Path(first.path).read_bytes() == b"first"
    assert Path(second.path).read_bytes() == b"second"
