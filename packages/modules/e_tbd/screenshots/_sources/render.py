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
import subprocess
import sys
import tempfile
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


def render(shot: dict, edge: Path, tmp: Path) -> Path:
    width = shot["variant"]["width"]
    height = estimate_height(shot)
    # low_res 用一半的視窗截，出來就真的是低解析度的圖
    scale = 0.5 if shot["variant"]["quality"] == "low_res" else 1.0

    html_file = tmp / f"{shot['id']}.html"
    html_file.write_text(build_html(shot), encoding="utf-8")
    out = SHOTS_DIR / f"{shot['id']}.png"

    subprocess.run(
        [
            str(edge),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--force-device-scale-factor={scale}",
            f"--screenshot={out}",
            f"--window-size={width},{height}",
            html_file.as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return out


def main() -> int:
    shots = yaml.safe_load((SOURCES_DIR / "shots.yaml").read_text(encoding="utf-8"))
    edge = find_edge()
    print(f"瀏覽器：{edge}")

    annotations: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for shot in shots:
            png = render(shot, edge, tmp)
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
