"""M1 語料處理：從全隊那份共用語料裡，把屬於自己的那幾千筆撈出來（S9）。

五個人的做法可以完全不一樣。這裡是最簡可跑版本：
讀 parquet → 依標籤與平台關鍵詞篩選 → 清理 → 存成自己的資料檔。

語料來源不綁死單一來源。165 是主語料（量最大、標籤是官方標的），但這支
只認下面 Case 那個正規化後的形狀 —— 任何來源只要能映射成它就能接進來。
新增來源要做的事寫在 data/README.md，重點是兩個難題：標籤體系要對齊，
以及平台欄位多半得從內文推斷。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
LOCAL_CORPUS = MODULE_DIR / "data" / "cases.jsonl"
# 共用語料放共用雲端硬碟，不進版控（§1.6）
# 預設檔名不帶來源名 —— 混來源時這份是正規化後的合併檔。只用 165 的話，
# 把 CORPUS_PARQUET 指到 cases_165.parquet 即可。
SHARED_CORPUS = Path(os.getenv("CORPUS_PARQUET", "data/corpus.parquet"))


@dataclass
class Case:
    """一筆案例 —— 各來源正規化之後的共同形狀。

    165 提供的六個欄位（編號、日期、縣市、縣市代號、內文、標籤）剛好對得上
    前五個。別的來源缺哪個就留空，但 case_id、text、source 三個一定要有：
    沒有 case_id 與 source 就給不出出處，外殼的 guards 會擋下來。
    """

    case_id: str
    text: str
    # 混來源時 case_id 會撞號（兩份資料各自從 1 開始編很正常），
    # 所以出處是 source + case_id，不是 case_id 自己。
    source: str = "165"
    label: str = ""
    date: str = ""
    county: str = ""
    tokens: list[str] = field(default_factory=list)


# 解析好的語料快取。鍵是（路徑, mtime, 檔案大小）—— 重切語料就自動失效。
_CACHE: tuple[tuple[str, int, int], list[Case]] | None = None


def count_local() -> int:
    """語料有幾筆。**不解析、不建 Case 物件。**

    health() 只需要知道「語料檔在不在、有幾筆」，但它原本是呼叫 load_local()
    ——整份 78 MB 讀進來、解析成 81,423 個物件，只為了取一個 bool。加上快取
    之後那 197 MB 還會從開機一路留到關機（實測：`load("all")` 一跑完 RSS 就是
    314 MB，其中 282 MB 是這件事，而且解析當下的高水位到 572 MB）。

    這支改成二進位分塊數 b"\n"：78 MB 約 30 毫秒、記憶體幾乎不動。

    數 b"\n" 跟 load_local() 的 split("\n") 等價 —— U+2028 在 UTF-8 裡是
    e2 80 a8，不含 0x0a，所以那個坑在這裡不存在（相對的，**不能**改用
    splitlines()，理由見 load_local()）。寫檔的那一端每筆後面都補 "\n"、
    不寫空行，所以換行數就是筆數；最後一位元組不是換行時補 1。

    語料已經在快取裡就直接回長度，不重讀。
    """
    if not LOCAL_CORPUS.exists():
        return 0
    stat = LOCAL_CORPUS.stat()
    stamp = (str(LOCAL_CORPUS), stat.st_mtime_ns, stat.st_size)
    if _CACHE is not None and _CACHE[0] == stamp:
        return len(_CACHE[1])
    n = 0
    last = b""
    with LOCAL_CORPUS.open("rb") as f:
        while chunk := f.read(1 << 20):
            n += chunk.count(b"\n")
            last = chunk[-1:]
    if last and last != b"\n":
        n += 1  # 最後一筆沒有換行結尾
    return n


def load_local() -> list[Case]:
    """讀自己切好的那一份。沒有就回空的 —— 不要在這裡爆炸。

    **會快取**。2026-09-24 語料從 17,764 筆擴到 81,423 筆之後，這支變成
    每次呼叫都要重讀、重新解析 78 MB 的 JSONL：實測 0.25 秒、約 197 MB。
    而 m3_retrieval.search() 不傳 cases 時每次查詢都會呼叫它一次 ——
    等於單次查詢的 404 毫秒裡有 250 毫秒花在重讀自己剛剛才讀過的檔案
    （17,764 筆時只花 0.05 秒，所以之前看不出來）。

    失效方式跟 m3_retrieval 的向量庫快取一樣看檔案的 mtime 與大小，不算雜湊
    —— 對 78 MB 算一次 sha256 要 0.05 秒，會把省下來的再吃掉一部分。代價是
    「同一微秒內把語料換成另一份一樣大的」偵測不到；語料是手動重切出來的，
    兩次之間差好幾秒，實務上碰不到。

    回傳的 list 每次都是新的（淺複製），所以呼叫端可以自由篩選、排序而不會
    污染快取。但 **Case 物件本身是共用的**，不要就地改它 —— 要改就自己建新的。
    m2_vision 的 OCR 快取踩過同一個坑（見那邊的註解）。
    """
    global _CACHE
    if not LOCAL_CORPUS.exists():
        return []
    stat = LOCAL_CORPUS.stat()
    stamp = (str(LOCAL_CORPUS), stat.st_mtime_ns, stat.st_size)
    if _CACHE is not None and _CACHE[0] == stamp:
        return list(_CACHE[1])
    cases: list[Case] = []
    # 🔴 一定要用 split("\n")，不能用 splitlines()。
    #
    # json.dumps(ensure_ascii=False) 只跳脫 \n \r \t " \\ 與 0x20 以下的控制字元，
    # **不跳脫** U+2028 LINE SEPARATOR、U+2029 PARAGRAPH SEPARATOR、U+0085 NEL。
    # 但 str.splitlines() 會在這三個上面斷行 —— 一筆合法的 JSONL 被切成兩半，
    # 讀回來就是 JSONDecodeError: Unterminated string。
    #
    # 2026-09-21 切到全量 17,764 筆時當場踩到：裡面有 2 個 U+2028，
    # splitlines() 數出 17,766 行、split("\n") 數出 17,764 行，
    # make index 直接掛在 health()。抽樣的 1,000 筆裡沒有，所以之前看不到。
    #
    # 2026-09-24 語料改成 81,423 筆之後是 5 個：splitlines() 數出 81,428 行。
    # 語料愈大這個坑愈深，但發生率不變 —— 靠抽樣永遠測不到它。
    #
    # 模組 C 在 b6387a8 就踩過同一個坑並記了下來（10,051 筆裡有 2 個），
    # 但那是他的資料夾，這邊沒跟著改。受害者的自由敘述什麼字元都有，
    # 用自己造的測試資料永遠碰不到這個。
    # 逐行串流，不要 read_text().split("\n")。
    #
    # 那個寫法會讓「整份 78 MB 的字串」「切出來的 81,423 個字串的 list」與
    # 「建好的 Case 物件」同時存在，實測解析當下的高水位比穩態高 258 MB。
    # 更糟的是這件事現在發生在 bge-m3（1.9 GB）載入之後 —— 配置器很難把那段
    # 還給系統，實測跑完整輪之後多留 106 MB。串流之後這兩個問題一起消失。
    #
    # newline="\n" 是必要的：不指定就是通用換行模式，\r 與 \r\n 也會斷行。
    # json.dumps 會把 \r 跳脫成 \\r，所以實務上不會有裸的 \r，但明著寫死
    # 比較不會在換了寫檔那端之後才發現。
    #
    # 這樣仍然只在 \n 斷行，U+2028 不受影響（它在 UTF-8 裡是 e2 80 a8）——
    # 那個坑只在 str.splitlines() 才會踩到，下面那段註解講的就是它。
    with LOCAL_CORPUS.open(encoding="utf-8", newline="\n") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            cases.append(
                Case(
                    case_id=str(raw.get("case_id", "")),
                    text=raw.get("text", ""),
                    source=raw.get("source", "165"),
                    label=raw.get("label", ""),
                    date=raw.get("date", ""),
                    county=raw.get("county", ""),
                )
            )
    _CACHE = (stamp, cases)
    return list(cases)


def norm_label(s: str) -> str:
    """標籤正規化。165 的官方標籤有前後空白與全形括號的變體 ——
    實測 50 組只差這些字元就會被當成不同類，涉及 191 種原始寫法。"""
    return re.sub(r"[\s\u3000]+", "", s).replace("（", "(").replace("）", ")")


# 黏在 LINE 前後、但講的確實是 LINE 的寫法。
#
# 2026-09-24 在全量語料上把「前後黏著英文字母的 line」掃過一遍：113 種變體，
# 其中 42 種會單獨決定一筆案件收不收（那些案件整篇沒有獨立的 line），
# 14 種要讀上下文才判得出來，逐筆讀完才列出下面這張表。
#
#   · 官方服務名：LINE ID 197／LINE Pay 35／LINE Bank 7／line.me 5／LINE QR 9
#   · 去識別化黏住：165 把姓名遮成半形 O，「我加她的助理許美OLINE聯繫方式」
#     就黏成 OLINE，「假買家LINEO宜（紫音媽咪）」同理 —— 只卡字界會誤殺這些
#   · 打字黏住：lLINE（「通訊軟體lLINE」）、LINEINE、PChomeLINE、usascLINE、
#     LINEOAD（LINE OA）、LINEHD（「LINEHD 共享資源群組」）
#
# 刻意不收的：online 361、celine 31、deadline 16、shopline 6、cityline 5、
# linear 8、skyline 3、lineup 1（服飾品牌官網）、linex 1（網址路徑 linex.html）、
# OMLINE 1（「星城OMLINE」是 Online 的錯字）。
#
# 這張表綁在這一份語料快照上。重抓語料要重掃一次 —— 受害者自由敘述裡的錯字
# 與遮罩形態沒辦法預先窮舉，用自己造的測試資料也碰不到。
LINE_GLUED = frozenset(
    {
        "lineid",
        "linepay",
        "linebank",
        "lineqrcode",
        "lineqr",
        "lineme",
        "lineine",
        "lline",
        "usascline",
        "lineoad",
        "pchomeline",
        "lineo",
        "oline",
        "linehd",
    }
)
_ASCII_TERM = re.compile(r"^[A-Za-z]+$")
_GLUED_SCAN = re.compile(r"[A-Za-z]*line[A-Za-z]*", re.I)


def matches_platform(text: str, platform_terms: list[str]) -> bool:
    """平台是從內文推斷的 —— 165 沒有平台欄位。

    純 ASCII 的詞卡英文字界、不分大小寫：實測 LINE / Line / line 三種寫法都
    有人用（全量 74,282／5,078／5,547 筆），只認大寫會漏掉九成。中文詞（加賴）
    照原樣比子字串。黏在別的字母裡的 line 預設不算，LINE_GLUED 那張表例外。

    2026-09-24 改的。原本是 `term.lower() in text.lower()` 的純子字串比對，
    在全量語料上會把 online／deadline／celine 一起收進來（182 筆）；但單純
    改成卡字界又會誤殺 LINEID／OLINE 這類真的案子（88 筆）。兩邊都要修，
    所以才有上面那張表。module.py 的 _platform_hit() 直接呼叫這支，兩邊
    不再各寫一套 —— 那正是這次要收掉的問題。
    """
    for term in platform_terms:
        if not term:
            continue
        if _ASCII_TERM.match(term):
            if re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", text, re.I):
                return True
        elif term in text:
            return True
    # 黏字表只在平台真的是 LINE 時才有意義（別人的平台詞也走這支）
    if not any(t.lower() == "line" for t in platform_terms if t):
        return False
    return any(m.group(0).lower() in LINE_GLUED for m in _GLUED_SCAN.finditer(text))


def build_subset(labels: list[str] | None, platform_terms: list[str], *, limit: int = 0) -> int:
    """從共用語料切出自己的那一份，寫進 data/cases.jsonl。

    一道必篩：內文要命中平台關鍵詞。labels 傳 None 就只篩平台，傳清單則
    另外要求標籤在裡面（正規化後比對）。limit > 0 時只取前幾筆。

    2026-09-24：A 改成 labels=None —— 語料是「165 全量裡提得到 LINE 的」
    81,423 筆，不再交集假投資系列標籤（原本 17,764 筆）。標籤篩選的程式碼
    留著是因為 pack.yaml 的 labels_canon 仍然在用（can_handle 的手法詞、
    m3_retrieval 的第一道門檻），只是不再拿來切語料。

    TODO(S9)：接上 CKIP 斷詞、把清理規則寫完整。目前沒有做斷詞，
    tokens 留空，檢索靠嵌入模型自己處理。
    """
    if not SHARED_CORPUS.exists():
        raise FileNotFoundError(
            f"找不到共用語料 {SHARED_CORPUS}。語料放共用雲端硬碟，"
            f"路徑用環境變數 CORPUS_PARQUET 指定（見 data/README.md）。"
        )

    import pyarrow.parquet as pq

    wanted = {norm_label(x) for x in labels} if labels else None
    rows = pq.read_table(SHARED_CORPUS).to_pylist()

    kept: list[dict] = []
    for r in rows:
        if wanted is not None and norm_label(r.get("label", "")) not in wanted:
            continue
        text = r.get("text", "") or ""
        if not matches_platform(text, platform_terms):
            continue
        kept.append(
            {
                "case_id": str(r.get("case_id", "")),
                "source": r.get("source", "165"),
                "text": text,
                "label": r.get("label", ""),
                "date": r.get("date", ""),
                "county": r.get("county", ""),
            }
        )
        if limit and len(kept) >= limit:
            break

    LOCAL_CORPUS.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_CORPUS.open("w", encoding="utf-8") as f:
        for rec in kept:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(kept)


def stats(cases: list[Case]) -> dict[str, int]:
    return {
        "total": len(cases),
        "labels": len({c.label for c in cases if c.label}),
        "counties": len({c.county for c in cases if c.county}),
    }


if __name__ == "__main__":
    # 語料不進版控，所以切語料這件事要能一行重跑：
    #   uv run python -m modules.a_tbd.m1_corpus
    # 讀 pack.yaml 的 platform_terms，寫出 data/cases.jsonl。
    import yaml

    pack = yaml.safe_load((MODULE_DIR / "pack.yaml").read_text(encoding="utf-8"))
    # labels 傳 None —— A 不做標籤切片，見 build_subset 的註解
    n = build_subset(None, pack["platform_terms"])
    print(f"切出 {n:,} 筆 → {LOCAL_CORPUS}")
