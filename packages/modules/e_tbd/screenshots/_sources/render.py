"""把 shots.yaml 畫成 PNG，同時產生 annotations.yaml（S11）。

為什麼用 HTML 排版再截圖，而不是手動做圖：

  1. 說明書 S11 要求「絕對不要用真實受害者的截圖，全部自己做」。
     原始碼進版控，任何人都能重現，也能檢驗我們沒有用到真實資料。
  2. 圖跟標註來自同一份 shots.yaml，所以標註不可能跟畫面對不上。
  3. 要調整版型或補圖時，改 YAML 重跑就好。

變化怎麼做（說明書要求不同解析度、深色淺色、有的故意拍糊）：

  sharp    照原尺寸截
  low_res  用一半的視窗寬度截 —— 真的是低解析度，不是假裝的
  blurred  CSS filter: blur()

用法：
    uv run python packages/modules/e_tbd/screenshots/_sources/render.py
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import yaml

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

SOURCES_DIR = Path(__file__).resolve().parent
SHOTS_DIR = SOURCES_DIR.parent

EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

# 淺色 / 深色兩組配色。深色不是把顏色反過來就好 ——
# 對話氣泡的左右判定在深色下是另一組顏色條件，這正是 S11 要測的東西。
THEMES = {
    "light": {
        "page": "#f2f3f5",
        "card": "#ffffff",
        "text": "#1c1e21",
        "muted": "#65676b",
        "line": "#e4e6eb",
        "accent": "#1877f2",
        "chat_bg": "#8ab4d8",
        "bubble_other": "#ffffff",
        "bubble_other_text": "#1c1e21",
        "bubble_self": "#8de055",
        "bubble_self_text": "#1c1e21",
        "header": "#ffffff",
    },
    "dark": {
        "page": "#18191a",
        "card": "#242526",
        "text": "#e4e6eb",
        "muted": "#b0b3b8",
        "line": "#3a3b3c",
        "accent": "#2d88ff",
        "chat_bg": "#1c2733",
        "bubble_other": "#2f3136",
        "bubble_other_text": "#e4e6eb",
        "bubble_self": "#2f6b34",
        "bubble_self_text": "#e9f5e4",
        "header": "#242526",
    },
}

BASE_CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family:"Microsoft JhengHei","PingFang TC","Noto Sans TC",sans-serif;
  background:{page}; color:{text}; width:{width}px; overflow:hidden;
  {blur}
}
.wrap { min-height:{height}px; }
.bar {
  background:{header}; border-bottom:1px solid {line};
  padding:14px 16px; font-size:17px; font-weight:600;
  display:flex; align-items:center; gap:10px;
}
.bar .back { color:{muted}; font-weight:400; }
.card { background:{card}; padding:18px 20px; }
.title { font-size:22px; font-weight:700; line-height:1.4; margin-bottom:6px; }
.price { font-size:20px; font-weight:700; color:#d93025; margin:10px 0; }
.field { font-size:15px; color:{muted}; line-height:1.9; }
.body  { font-size:16px; line-height:1.75; margin:10px 0; }
.button {
  display:inline-block; background:{accent}; color:#fff; font-size:17px;
  font-weight:600; padding:12px 28px; border-radius:6px; margin-top:16px;
}
.sep { height:8px; background:{page}; }
"""

CHAT_CSS = """
.chat { background:{chat_bg}; padding:16px 12px; min-height:{height}px; }
.row { display:flex; margin-bottom:14px; }
.row.self { justify-content:flex-end; }
.bubble {
  max-width:74%; padding:11px 14px; border-radius:16px;
  font-size:16px; line-height:1.6; word-break:break-word;
}
.row.other .bubble { background:{bubble_other}; color:{bubble_other_text};
  border-top-left-radius:4px; }
.row.self  .bubble { background:{bubble_self};  color:{bubble_self_text};
  border-top-right-radius:4px; }
.row.system { justify-content:center; }
.row.system .bubble {
  background:{card}; color:{text}; max-width:88%;
  border:1px solid {line}; border-radius:10px; font-size:15px;
}
.sender { font-size:13px; color:{muted}; margin:0 0 4px 6px; }
"""

PROFILE_CSS = """
.pf { background:{card}; text-align:center; padding:34px 20px 26px; }
.avatar {
  width:92px; height:92px; border-radius:50%; background:{line};
  margin:0 auto 14px; display:flex; align-items:center; justify-content:center;
  font-size:34px; color:{muted};
}
.pf .title { font-size:23px; }
.pf .field { font-size:15px; }
.pf .body { font-size:15px; color:{muted}; margin-top:12px; }
.pf .button { width:82%; text-align:center; }
"""

RECEIPT_CSS = """
.receipt {
  background:{card}; margin:18px; padding:22px 20px;
  border:1px solid {line}; border-radius:4px;
}
.receipt .title {
  font-size:19px; text-align:center; padding-bottom:12px;
  border-bottom:2px solid {text}; margin-bottom:14px;
}
.receipt .field {
  font-size:15px; color:{text}; padding:7px 0;
  border-bottom:1px dashed {line}; display:flex; justify-content:space-between;
}
.receipt .price { text-align:right; font-size:17px; }
"""


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fill(css: str, theme: dict[str, str], width: int, height: int, blur: str) -> str:
    out = css
    for key, value in theme.items():
        out = out.replace("{" + key + "}", value)
    return (
        out.replace("{width}", str(width)).replace("{height}", str(height)).replace("{blur}", blur)
    )


def estimate_height(shot: dict) -> int:
    """粗估畫面高度。寧可高一點留白，也不要把內容截掉。"""
    blocks = shot["blocks"]
    if shot["layout"] == "chat":
        return 90 + sum(60 + 26 * (len(b["text"]) // 18) for b in blocks)
    per_role = {"title": 46, "price": 44, "field": 34, "body": 60, "button": 72}
    return 120 + sum(per_role.get(b.get("role", "body"), 40) for b in blocks)


def build_html(shot: dict) -> str:
    theme = THEMES[shot["variant"]["theme"]]
    width = shot["variant"]["width"]
    height = estimate_height(shot)
    blur = "filter:blur(1.1px);" if shot["variant"]["quality"] == "blurred" else ""
    layout = shot["layout"]
    blocks = shot["blocks"]

    css = BASE_CSS
    if layout == "chat" or layout == "sms":
        css += CHAT_CSS
    elif layout == "profile":
        css += PROFILE_CSS
    elif layout == "product":
        css += RECEIPT_CSS
    css = _fill(css, theme, width, height, blur)

    parts: list[str] = []
    if layout in ("chat", "sms"):
        # 第一個 title 當成對話對象的名稱，放在頂欄
        header = next((b["text"] for b in blocks if b.get("role") == "title"), "聊天")
        body_blocks = [b for b in blocks if b.get("role") != "title"]
        parts.append(f'<div class="bar"><span class="back">&lt;</span>{_esc(header)}</div>')
        parts.append('<div class="chat">')
        prev = None
        for b in body_blocks:
            spk = b.get("speaker") or "other"
            # 群組畫面要標出是誰在講，這是多發話人分群的線索
            if shot["id"].endswith("group") and spk == "other" and spk != prev:
                parts.append('<div class="sender">工作群組成員</div>')
            parts.append(
                f'<div class="row {spk}"><div class="bubble">{_esc(b["text"])}</div></div>'
            )
            prev = spk
        parts.append("</div>")
    elif layout == "profile":
        parts.append('<div class="bar">個人檔案</div><div class="pf">')
        parts.append('<div class="avatar">&#128100;</div>')
        for b in blocks:
            parts.append(f'<div class="{b.get("role", "body")}">{_esc(b["text"])}</div>')
        parts.append("</div>")
    elif layout == "product":
        parts.append('<div class="bar">寄件明細</div><div class="receipt">')
        for b in blocks:
            parts.append(f'<div class="{b.get("role", "body")}">{_esc(b["text"])}</div>')
        parts.append("</div>")
    else:  # ad_post
        parts.append('<div class="card">')
        for b in blocks:
            parts.append(f'<div class="{b.get("role", "body")}">{_esc(b["text"])}</div>')
        parts.append("</div>")

    return (
        "<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
        f"<style>{css}</style></head><body><div class='wrap'>"
        + "".join(parts)
        + "</div></body></html>"
    )


def find_edge() -> Path:
    for path in EDGE_CANDIDATES:
        if path.exists():
            return path
    found = shutil.which("msedge") or shutil.which("chrome")
    if found:
        return Path(found)
    raise SystemExit("找不到 Edge 或 Chrome，無法產生截圖")


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def sample_colours(png: Path) -> set[tuple[int, int, int]]:
    """PNG 裡出現過哪些顏色（跳點取樣）。純標準庫解碼，不引入 Pillow
    （專案核心相依是凍結的共管路徑，不能為了一支工具腳本去動它）。"""
    data = png.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return set()

    pos, idat, width, height, colortype = 8, b"", 0, 0, 0
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        if ctype == b"IHDR":
            width, height, _, colortype = struct.unpack(">IIBB", data[pos + 8 : pos + 18])
        elif ctype == b"IDAT":
            idat += data[pos + 8 : pos + 8 + length]
        elif ctype == b"IEND":
            break
        pos += 12 + length

    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colortype)
    if not channels or not width:
        return set()
    stride = width * channels
    raw = zlib.decompress(idat)

    # PNG 的每一行都以前一行為基準做差分，所以不能跳行，只能逐行還原
    seen: set[tuple[int, int, int]] = set()
    prev, i = bytearray(stride), 0
    for y in range(height):
        if i + 1 + stride > len(raw):
            break
        f, line = raw[i], bytearray(raw[i + 1 : i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if f == 1:
                line[x] = (line[x] + a) & 0xFF
            elif f == 2:
                line[x] = (line[x] + b) & 0xFF
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        prev = line
        if y % 4 == 0:  # 取樣才跳行，還原不跳
            for x in range(0, width, 4):
                px = line[x * channels : x * channels + 3]
                if len(px) == 3:
                    seen.add((px[0], px[1], px[2]))
    return seen


def kill_stragglers(tmp: Path) -> None:
    """收掉這次自己叫起來、還沒退場的 Edge。

    Edge 的第一個程序只是 launcher，它把工作丟給背景的 browser process 就自己
    退出了（實測 0.0 秒返回），所以 subprocess 回來不代表瀏覽器結束。不收的話
    20 張跑下來會累積幾十個程序互相搶資源，後面的圖就整批截不出來
    —— 2026-09-23 實測堆到 60 個之後連第一張都失敗。

    只殺 --user-data-dir 指向本次暫存目錄的，使用者自己開的 Edge 視窗不會被動到。
    """
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{tmp.name}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
            "-ErrorAction SilentlyContinue }",
        ],
        capture_output=True,
        timeout=60,
    )


def render(shot: dict, edge: Path, tmp: Path, idx: int) -> Path:
    width = shot["variant"]["width"]
    height = estimate_height(shot)
    # low_res 用一半的視窗截，出來就真的是低解析度的圖
    scale = 0.5 if shot["variant"]["quality"] == "low_res" else 1.0

    # 工作目錄的名字刻意取得極短（tmp/12/p3）。Edge 會在 --user-data-dir 底下
    # 再建 Default\Cache\Cache_Data\… 一長串，加上外層路徑很容易超過 Windows
    # 的 260 字元上限；超過的時候 Edge 不報錯，回傳 0、0.0 秒就退出、什麼都不寫。
    work = tmp / str(idx)
    work.mkdir(parents=True, exist_ok=True)
    html_file = work / "page.html"
    html_file.write_text(build_html(shot), encoding="utf-8")
    out = SHOTS_DIR / f"{shot['id']}.png"
    out.unlink(missing_ok=True)

    # 每張給獨立的 user-data-dir。共用預設設定檔時，連續啟動的 headless
    # 實例會互相卡住，而且 Edge 仍然回傳 0 —— check=True 攔不到，
    # 結果是留下 0 KB 的圖配上完整的標註（2026-09-23 實測 20 張壞 13 張）。
    theme = THEMES[shot["variant"]["theme"]]
    # 截對了的畫面一定看得到自己的底色：版面刻意估高留白，露出 body 的 page 色，
    # 對話版型則整片是 chat_bg。Edge 的錯誤頁兩個都不會有。
    wanted = {_rgb(theme["page"]), _rgb(theme["chat_bg"])}

    for attempt in (1, 2, 3, 4, 5):
        # 每次都截到不同的暫存檔，最後才複製到 screenshots/ ——
        # subprocess.run 回來時 Edge 的子程序可能還活著，晚幾秒才把畫面寫出來。
        # 讓它直接寫最終路徑的話，會把上一輪已經驗證過的好圖蓋成錯誤頁，
        # 而且 render 早就印完 [OK] 了（2026-09-23 就是這樣被騙過去的）。
        staged = work / f"s{attempt}.png"
        subprocess.run(
            [
                str(edge),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                # 沒有這行，Edge 會在頁面排版完成前就截圖然後什麼也不寫，
                # 而且回傳 0 —— check=True 攔不到
                "--virtual-time-budget=3000",
                f"--user-data-dir={work / ('p' + str(attempt))}",
                f"--force-device-scale-factor={scale}",
                f"--screenshot={staged}",
                f"--window-size={width},{height}",
                html_file.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        # subprocess 回來只代表 launcher 退場，圖還在背景寫（見 kill_stragglers），
        # 所以自己等檔案出現
        deadline = time.time() + 45
        while time.time() < deadline:
            if staged.exists() and staged.stat().st_size > 0:
                time.sleep(1.0)  # 讓它把檔案寫完整再讀
                break
            time.sleep(0.5)
        kill_stragglers(tmp)

        # 檔案存在也還不算數：Edge 偶爾會導航失敗，把 ERR_FILE_NOT_FOUND
        # 的錯誤頁截下來給你 —— 有效的 PNG、大小正常，只有內容是錯的
        if staged.exists() and staged.stat().st_size > 0 and sample_colours(staged) & wanted:
            shutil.copyfile(staged, out)
            return out
        time.sleep(2)  # 機器忙的時候（例如同時在建向量庫）讓它喘一口

    # 寧可整批失敗也不要放過壞圖 —— 圖是空的或錯的但標註齊全，比沒有更難發現
    raise RuntimeError(f"{shot['id']} 連續 {attempt} 次都沒截到正確的畫面")


def main() -> int:
    shots = yaml.safe_load((SOURCES_DIR / "shots.yaml").read_text(encoding="utf-8"))
    edge = find_edge()
    print(f"瀏覽器：{edge}")

    annotations: list[dict] = []
    # 暫存放系統 %TEMP%（路徑短，見 render 裡關於 260 字元的註解）。
    # ignore_cleanup_errors：Edge 結束後還會壓著 profile 目錄裡的 lockfile 一下下，
    # 清不掉不是錯誤，圖已經截好了。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        for idx, shot in enumerate(shots):
            png = render(shot, edge, tmp, idx)
            size = png.stat().st_size if png.exists() else 0
            status = "OK" if size else "失敗"
            print(f"  [{status}] {png.name}  {size // 1024} KB  ({shot['layout']})")
            annotations.append(
                {
                    "file": png.name,
                    "layout": shot["layout"],
                    "variant": shot["variant"],
                    "is_scam": shot["is_scam"],
                    "blocks": shot["blocks"],
                    "notes": shot["notes"],
                }
            )

    (SHOTS_DIR / "annotations.yaml").write_text(
        "# 由 _sources/render.py 產生，不要手改 —— 改 _sources/shots.yaml 後重跑。\n"
        "# 標註與畫面同源，所以不可能對不上。\n\n"
        + yaml.safe_dump(
            {
                "format_version": 1,
                "module": "e_tbd",
                "annotator": "c85016921920-design",
                "screenshots": annotations,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    print(f"\n完成：{len(annotations)} 張，標註寫入 annotations.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
