"""產生專題主要架構圖：兩個檔案、同一份內容。

    docs/img/main-architecture.svg   GitHub 上看的圖（docs/architecture.md 嵌的就是它）
    docs/main-architecture.html      網頁版：手機也能看，每個檔名都連到 GitHub

內容只寫在這支檔案上半部的「內容」那一段，兩種輸出都從那裡畫出來 ——
不會發生「圖改了、網頁忘了改」。程式改了、流程變了，改內容再重跑：

    uv run python tools/gen_main_architecture.py

只用標準函式庫。SVG 的版面由程式算：每段字先估寬度，超出欄寬就印警告，
照警告改字或斷行（SVG 的文字不會自己換行）。

--fragment PATH 另外輸出不含 <!doctype>/<html>/<body> 的網頁片段，
發布到 claude.ai 的 Artifact 時用（那邊會自己包外殼）。
"""

from __future__ import annotations

import argparse
import html
import sys
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG_OUT = ROOT / "docs" / "img" / "main-architecture.svg"
HTML_OUT = ROOT / "docs" / "main-architecture.html"

# ══════════════════════════════════════════════════════════════
#  內容（改圖只改這一段）
# ══════════════════════════════════════════════════════════════

REPO = "https://github.com/Sinediesoft/Anti-fraud-Agentic-Rag"
BRANCH = "a-rag"  # 合併進 main 之後改成 "main"，網頁上的程式碼連結會跟著換
COMMIT = "2c2662f"
VERIFIED = "2026-09-24"


@dataclass(frozen=True)
class Ref:
    """一個程式碼位置。path 是顯示用的：modules/、shared/ 省略 packages/。"""

    path: str
    funcs: str = ""
    note: str = ""
    who: str = ""  # 「A」「E」這類前綴，SVG 右欄用來分辨是哪個模組的
    link: str = ""  # 連結改指到別處（repo 相對路徑）；空字串就用 path
    tree: bool = False  # 連到資料夾而不是檔案

    @property
    def url(self) -> str:
        target = self.link or self.path
        if target.startswith(("modules/", "shared/")):
            target = "packages/" + target
        return f"{REPO}/{'tree' if self.tree else 'blob'}/{BRANCH}/{target}"


def each(path: str, funcs: str = "", note: str = "") -> Ref:
    """modules/*/… 這種每個模組都有一份的檔案，連到 packages/modules/ 資料夾。"""
    return Ref(path, funcs, note, link="modules", tree=True)


@dataclass(frozen=True)
class Text:
    """SVG 要自己斷行，網頁不用：lines 是斷好的行，html 是網頁上的整句。"""

    lines: tuple[str, ...]
    html: str = ""

    @property
    def joined(self) -> str:
        return self.html or "".join(self.lines)


def T(*lines: str, html: str = "") -> Text:
    return Text(tuple(lines), html)


@dataclass(frozen=True)
class Act:
    text: str
    refs: tuple[Ref, ...] = ()


def act(text: str, *refs: Ref) -> Act:
    return Act(text, refs)


@dataclass(frozen=True)
class Step:
    n: str
    title: str
    acts: tuple[Act, ...]
    chips: tuple[tuple[str, str], ...]
    tag: str = ""  # 標題旁的補充，例如「每個行程一次」
    edge: str = ""  # 往下一步的箭頭上寫的字


def none3() -> tuple[tuple[str, str], ...]:
    return (("無模型（規則）", "none"), ("CPU", "none"), ("記憶體", "none"))


def cpu3(model: str) -> tuple[tuple[str, str], ...]:
    return ((model, "cpu"), ("CPU", "cpu"), ("記憶體", "cpu"))


def gpu3(model: str = "qwen2.5:3b") -> tuple[tuple[str, str], ...]:
    return ((model, "gpu"), ("GPU", "gpu"), ("顯卡記憶體", "gpu"))


TITLE = "防詐 Copilot 主要架構"
SVG_TITLE = "防詐 Copilot 主要架構：每一步呼叫的模型、跑在 CPU 或 GPU、載入哪一種記憶體"
LEDE = (
    "從使用者輸入、A～E 模組到 M5 Agent，每一步呼叫的模型、跑在 CPU 或 GPU、"
    "載入記憶體還是顯卡記憶體，以及對應的程式碼。"
)
STATUS = f"專題主要架構（{VERIFIED} 定案）。其他流程圖已列為廢案，保存在 docs/廢案/。"
SOURCE = (
    f"現況對到 {BRANCH} 的 {COMMIT}。{VERIFIED} 在 A 的 Mac（M5）實跑確認：執行紀錄的步驟順序、"
    "ollama ps、onnxruntime 的 providers、bge-m3 參數所在的裝置。"
)

LEGEND = (
    ((("GPU", "gpu"), ("顯卡記憶體", "gpu")), "小模型 qwen2.5:3b（在 Ollama 裡跑）"),
    ((("CPU", "cpu"), ("記憶體", "cpu")), "嵌入 bge-m3、OCR PP-OCRv6 small"),
    ((("無模型（規則）", "none"),), "詞表、規則比對"),
)

STEP0 = Step(
    "0",
    "開機：載入外殼、模組與嵌入模型",
    (
        act("畫出介面：Streamlit，兩個分頁（對話／單次查詢）", Ref("app/ui.py", "main()")),
        act("建外殼：註冊表＋解鎖層；畫面每次重跑都會重建", Ref("app/shell.py", "Shell.boot()")),
        act(
            "掃模組：A、C、E 載入；B、D 沒有 pack.yaml，掃不到",
            Ref("app/registry.py", "load()、discover()"),
        ),
        act(
            "各模組回報健康狀態（會讀自己的語料檔）",
            each("modules/*/module.py", "build_module()、health()"),
        ),
        act("背景執行緒預熱 bge-m3，使用者打字時就載完", Ref("app/ui.py", "_warm_up_embedder()")),
        act(
            "載入權重：寫死 CPU、fp32；整個行程只載一次",
            Ref("shared/models.py", "embed()、_load_embedder()"),
        ),
    ),
    (("bge-m3　568M 參數・fp32", "cpu"), ("CPU", "cpu"), ("記憶體 約 2.2 GB", "cpu")),
    tag="每個行程一次",
    edge="開機完成，等使用者開口",
)

STEP1 = Step(
    "1",
    "使用者輸入：打字，可以附截圖",
    (
        act(
            "送出這一句；上傳框裡的截圖跟著一起交出",
            Ref("app/ui.py", "_chat_tab()、_uploads_to_images()"),
        ),
        act(
            "截圖存到本機暫存目錄，檔名用內容雜湊（同一張只存一份）",
            Ref("app/uploads.py", "save_upload()"),
        ),
        act(
            "同一張圖只加一次；這一句併進對話紀錄",
            Ref("app/chat.py", "ChatSession.add_images()、send()"),
        ),
    ),
    none3(),
)
LOOP = "還沒問滿三題就回到 ①"

STEP2 = Step(
    "2",
    "對話層：追問三輪",
    (
        act(
            "每一輪先跑一次 ③ 的路由，只為了畫「分數怎麼變」那張圖",
            Ref("app/chat.py", "_advance()、_scores()"),
        ),
        act(
            "偵測到受災訊號，先給停損三條與 165（不等追問完）",
            Ref("app/chat.py", "_distress_advice()"),
        ),
        act(
            "挑還沒講過的格子問；追問用的詞彙讀各模組的 pack.yaml",
            Ref("app/chat.py", "_filled()、_next_question()"),
        ),
    ),
    none3(),
    edge="問滿三題，或按「直接看結果」→ shell.analyze()",
)


@dataclass(frozen=True)
class Tile:
    """路由那一步的一個模組。"""

    code: str
    name: str
    sub: str
    width: float  # SVG 裡的欄寬
    body: tuple[Text, ...]
    chips: tuple[tuple[str, str], ...] = ()
    after: Text | None = None
    refs: tuple[Ref, ...] = ()
    stub: bool = False


ROUTE_TITLE = "路由：A～E 每個模組自己打分 can_handle()"
ROUTE_ACT = act(
    "問每個載入的模組；分數過自己的 route_min 才算認領", Ref("app/router.py", "route()、_ask()")
)
ROUTE_EDGE = "收齊 A、C、E 的分數"
TILES = (
    Tile(
        "A",
        "LINE",
        "假投資・免費",
        176,
        (T("有截圖先用 OCR 讀出字"),),
        cpu3("PP-OCRv6 small"),
        T("第一張圖進來才載入引擎，", "結果依圖檔雜湊快取；", "再算關鍵字分數 × 平台係數"),
        (
            Ref("modules/a_tbd/module.py", "can_handle()"),
            Ref("modules/a_tbd/m2_vision.py", "read_all()"),
            Ref("modules/a_tbd/m4_judgement.py", "keyword_score()"),
        ),
    ),
    Tile(
        "B",
        "",
        "",
        70,
        (T("尚未建立", "題目還沒", "宣告", html="尚未建立，題目還沒宣告"),),
        refs=(Ref("modules/b_tbd/README.md", note="只有這個檔"),),
        stub=True,
    ),
    Tile(
        "C",
        "Facebook",
        "投資詐騙・付費",
        124,
        (T("關鍵字＋平台加分"), T("截圖不處理（空殼）")),
        (("無模型（規則）", "none"), ("CPU", "none")),
        refs=(
            Ref("modules/c_tbd/module.py", "can_handle()"),
            Ref("modules/c_tbd/m2_vision.py", "read_screenshot()", "空殼"),
        ),
    ),
    Tile(
        "D",
        "",
        "",
        70,
        (T("尚未建立", "只有題目", "README", html="尚未建立，只有題目 README"),),
        refs=(Ref("modules/d_tbd/README.md", note="只有這個檔"),),
        stub=True,
    ),
    Tile(
        "E",
        "Threads",
        "網購詐騙・付費",
        124,
        (T("關鍵字＋決定性詞", "＋平台加分"), T("截圖不處理（空殼）")),
        (("無模型（規則）", "none"), ("CPU", "none")),
        refs=(
            Ref("modules/e_tbd/module.py", "can_handle()"),
            Ref("modules/e_tbd/m2_vision.py", "read_screenshot()", "空殼"),
        ),
    ),
)

STEP4 = Step(
    "4",
    "選模組：只交給分數最高的一個",
    (
        act("分數最高的出完整判讀；打平時看 priority", Ref("app/router.py", "route()")),
        act(
            "其他過門檻或接近門檻的，只出「你可能同時也遇到」",
            Ref("app/shell.py", "Shell.analyze()"),
        ),
        act("選中的模組先過解鎖檢查（A 免費，C、E 付費）", Ref("app/entitlements.py", "can_use()")),
        act("沒有模組過門檻 → 分支「尚未涵蓋」", Ref("app/shell.py", "GENERAL_ADVICE")),
        act("選中的模組未解鎖 → 分支「未解鎖」", Ref("app/entitlements.py", "locked_notice()")),
    ),
    none3(),
    edge="有模組認領、而且已解鎖",
)
BRANCHES = (
    ("尚未涵蓋", "只給通用建議＋165 → ⑥"),
    ("未解鎖", "只說類型，風險與 165 照給 → ⑥"),
)


@dataclass(frozen=True)
class Cell:
    lines: Text
    chips: tuple[tuple[str, str], ...] = ()
    small: Text | None = None
    kind: str = "normal"  # normal / stub（空殼）/ skip（沒有這一步）
    span: int = 1


@dataclass(frozen=True)
class AgentRow:
    label: tuple[str, ...]
    cells: tuple[Cell, ...]
    refs: tuple[Ref, ...]


AGENT_TITLE = "M5 Agent：被選中的模組跑自己的流水線"
AGENT_ACTS = (
    act(
        "analyze() → m5_agent.run()：由上而下依序執行；一步壞掉只降級，不中斷",
        each("modules/*/module.py", "analyze()"),
        each("modules/*/m5_agent.py", "run()、_step()"),
    ),
    act("B、D 還沒有模組，不會走到這一段"),
)
AGENT_COLS = ("A　LINE 假投資", "C　Facebook 投資", "E　Threads 網購")
AGENT_ROWS = (
    AgentRow(
        ("m2", "截圖理解"),
        (
            Cell(T("路由時辨識過就直接讀快取"), cpu3("PP-OCRv6 small")),
            Cell(T("空殼：截圖不處理"), kind="stub"),
            Cell(T("空殼：截圖不處理"), kind="stub"),
        ),
        (
            Ref("modules/a_tbd/m2_vision.py", "read_all()", who="A"),
            Ref("modules/c_tbd/m2_vision.py", note="空殼", who="C"),
            Ref("modules/e_tbd/m2_vision.py", note="空殼", who="E"),
        ),
    ),
    AgentRow(
        ("合併輸入",),
        (Cell(T("打字＋截圖上的文字接成一段"), none3(), span=3),),
        (each("modules/*/m5_agent.py", "combine()", "run() 裡的內部函式"),),
    ),
    AgentRow(
        ("去識別化", "shared.deid"),
        (Cell(T("規則比對遮個資（NER 還沒接）"), none3(), span=3),),
        (Ref("shared/deid.py", "mask()", "m5_agent.py 的 apply_deid() 呼叫它"),),
    ),
    AgentRow(
        ("m4", "分類與階段"),
        (
            Cell(T("線索詞規則判階段；小模型的 prompt 還沒寫，不呼叫"), none3(), span=2),
            Cell(T("先送空 prompt 給小模型、", "回答丟掉不用，", "再走 12 階段規則"), gpu3()),
        ),
        (
            each("modules/*/m4_judgement.py", "judge()"),
            Ref("modules/e_tbd/threads_stages.py", "assess()", who="E"),
            Ref("shared/models.py", 'call_slm("")', "空 prompt", who="E"),
        ),
    ),
    AgentRow(
        ("m3", "檢索相似案例"),
        (
            Cell(
                T(
                    "先過關鍵字門檻，",
                    "bge-m3 編碼問句，",
                    "和 17,764 筆向量算內積",
                    "＋手法詞加分，取前 5 筆",
                ),
                cpu3("bge-m3"),
                T("索引 73 MB，每次查詢", "從磁碟讀進記憶體"),
            ),
            Cell(
                T("bge-m3 編碼問句，", "和 10,051 筆向量算內積，", "門檻 0.60，取前 5 筆"),
                cpu3("bge-m3"),
                T("索引 64 MB，每次讀進", "記憶體，也整份寫回磁碟"),
            ),
            Cell(T("字元 2-gram 重疊比對，", "沒有向量"), none3()),
        ),
        (
            each("modules/*/m3_retrieval.py", "search()"),
            Ref("modules/a_tbd/m3_retrieval.py", "_gate_keyword()、_vector_scores()", who="A"),
            Ref("modules/c_tbd/m3_retrieval.py", "Retriever.retrieve()、NumpyStore", who="C"),
            Ref("shared/models.py", "embed()", "問句向量"),
        ),
    ),
    AgentRow(
        ("生成", "白話說明（G）"),
        (
            Cell(T("（沒有這一步）"), kind="skip"),
            Cell(
                T("qwen2.5:3b 讀遮蔽文字", "＋前 3 筆案例寫說明"),
                gpu3("qwen2.5:3b Q4_K_M"),
                T("失敗或超過 400 字，", "退回劇本的固定說明"),
            ),
            Cell(T("（沒有這一步）"), kind="skip"),
        ),
        (
            Ref("modules/c_tbd/generation.py", "explain()、build_prompt()", who="C"),
            Ref("shared/models.py", "call_slm()", "HTTP 送到 Ollama（127.0.0.1:11434）"),
        ),
    ),
    AgentRow(
        ("合成判讀",),
        (
            Cell(
                T("playbook.yaml 的階段 → 風險等級與行動清單，附相似案例與法條"),
                none3(),
                span=3,
            ),
        ),
        (
            each("modules/*/m5_agent.py", "_compose()"),
            each("modules/*/playbook.yaml", note="階段與行動清單"),
            each("modules/*/pack.yaml", note="knowledge_refs（法條）"),
        ),
    ),
)
AGENT_EDGE = "Verdict（判讀結果）"

STEP6 = Step(
    "6",
    "輸出檢核與呈現",
    (
        act(
            "高風險卻沒行動 → 改走通用建議；案例沒編號丟掉；免責聲明補回",
            Ref("app/guards.py", "enforce()、check()"),
        ),
        act("組成回應：判讀＋其他模組的提醒＋風險與 165", Ref("app/shell.py", "Shell.analyze()")),
        act("攤成對話訊息：純樣板，不生成新內容", Ref("app/chat.py", "_render()")),
        act("判讀之後的追問，只回答白名單裡的欄位", Ref("app/chat.py", "_follow_up()")),
        act(
            "右側畫執行紀錄與「每問一題，分數怎麼變」",
            Ref("app/ui.py", "_render_trace()、_chat_tab()"),
        ),
    ),
    none3(),
)

# 頁尾：b 開一條新的，c 接在上一條後面（網頁上併成同一段）
FOOT_TITLE = "裝置與記憶體的細節"
FOOT = (
    (
        "b",
        "GPU（C 的生成、E 的空呼叫）：qwen2.5:3b Q4_K_M 跑在 Ollama 自己的行程，不在 Python 裡；"
        "第一次呼叫才載入，閒置 5 分鐘自動卸載。",
    ),
    (
        "c",
        "A 的 Mac（M5）是 Metal＋統一記憶體，ollama ps 顯示 2.4 GB、100% GPU。B、C 的 GTX 1650 是 CUDA＋"
        "4 GB 顯卡記憶體，實測約 2.3 GB、100% GPU。",
    ),
    (
        "c",
        "D 的筆電沒有獨顯，Ollama 改裝在教室電腦（1050 Ti，4 GB，還沒實測）；沒有 GPU 的機器，Ollama 會退回 CPU 跑。",
    ),
    ("c", "E 的 5070 Ti 有 16 GB，但全隊用同一個 3B 模型；num_ctx 一律鎖 8192。"),
    (
        "b",
        "CPU（bge-m3、OCR）：五台都一樣。bge-m3 寫死 cpu＋fp32，分數才能互比（實測參數在 cpu、float32）；"
        "OCR 的 onnxruntime 實測只開 CPUExecutionProvider。",
    ),
    (
        "c",
        "兩者都在 Streamlit 的 Python 行程裡，載一次共用：bge-m3 約 2.2 GB；OCR 模型檔約 32 MB，"
        "跑圖時行程記憶體會長到約 1.5 GB。",
    ),
    (
        "b",
        "不在查詢路徑上：重排序 bge-reranker-v2-m3（rerank() 還沒實作，決議只用在評估）；"
        "雲端模型（沒有任何地方呼叫 call_cloud()）。",
    ),
)

# 網頁版開頭的摘要：三個模型住在哪裡
MODELS = (
    dict(
        name="bge-m3",
        sub="568M 參數・fp32",
        where="開機時預熱（步驟 0）；A、C 的檢索（步驟 5）",
        device=("CPU（寫死）", "cpu"),
        mem=("記憶體・約 2.2 GB", "cpu"),
        proof="參數在 cpu、float32",
    ),
    dict(
        name="PP-OCRv6 small",
        sub="RapidOCR 3.9.2 內建的 ONNX 檔",
        where="A 路由打分時讀截圖（步驟 3）；A 的 m2 讀快取（步驟 5）",
        device=("CPU（onnxruntime）", "cpu"),
        mem=("記憶體・跑久了約 1.5 GB", "cpu"),
        proof="三個 session 都只開 CPUExecutionProvider",
    ),
    dict(
        name="qwen2.5:3b",
        sub="Q4_K_M・digest 357c53fb659c",
        where="C 的生成、E 的 m4 空呼叫（步驟 5）",
        device=("GPU（Metal／CUDA）", "gpu"),
        mem=("顯卡記憶體・約 2.4 GB", "gpu"),
        proof="ollama ps：100% GPU",
    ),
)

# ══════════════════════════════════════════════════════════════
#  共用：估字寬
# ══════════════════════════════════════════════════════════════

warnings: list[str] = []
PUNCT_FULL = set("，。、：；（）「」『』—…～＋×・　？！●")


def tw(s: str, size: float, mono: bool = False) -> float:
    """估文字寬度。寧可估大，不要溢出。"""
    w = 0.0
    for ch in s:
        o = ord(ch)
        if o >= 0x2E80 or ch in PUNCT_FULL or 0x2190 <= o <= 0x21FF or 0x2460 <= o <= 0x24FF:
            w += size
        elif mono:
            w += size * 0.61
        elif ch in "il.,:;|!'`()[]{}":
            w += size * 0.34
        elif ch in "mwMW@%":
            w += size * 0.88
        elif ch.isupper():
            w += size * 0.7
        elif ch.isdigit():
            w += size * 0.6
        elif ch == " ":
            w += size * 0.3
        else:
            w += size * 0.57
    return w


# ══════════════════════════════════════════════════════════════
#  SVG（GitHub 上看的那張）
# ══════════════════════════════════════════════════════════════

W = 1140
X0 = 56  # 流程欄左緣（左邊留給追問迴圈的線）
FW = 640  # 流程欄寬
NX = X0 + FW + 28  # 程式碼欄左緣
NW = W - 24 - NX  # 程式碼欄寬
SPINE = X0 + 195  # 主幹箭頭的 x
GAP = 40  # 區塊之間的箭頭長度
LH = 18  # 流程欄行高
NLH = 15  # 程式碼欄行高
CHIP_H = 18
CHIP_FS = 11
CHIP_PAD = 7
CHIP_GAP = 5
TEXT_X = X0 + 54
DOT_X = X0 + 45
NOTE_DOT_X = NX + 12
NOTE_X = NX + 22
NOTE_MAX = NX + NW - 10 - NOTE_X
TC = 11.5  # 格子內字級
TCL = 16  # 格子內行高

svg: list[str] = []


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def check(s: str, size: float, max_w: float, where: str, mono: bool = False) -> None:
    w = tw(s, size, mono)
    if w > max_w + 0.5:
        warnings.append(f"溢出 {w:.0f}>{max_w:.0f}  [{where}] {s}")


def text(x, y, s, cls, anchor=None, max_w=None, where="", mono=False, size=12.0):
    if max_w is not None:
        check(s, size, max_w, where, mono)
    a = f' text-anchor="{anchor}"' if anchor else ""
    svg.append(
        f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}"{a} dominant-baseline="central">{esc(s)}</text>'
    )


def rect(x, y, w, h, cls, rx=8):
    svg.append(
        f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}"/>'
    )


def line(x1, y1, x2, y2, cls, arrow=False):
    m = ' marker-end="url(#arrow)"' if arrow else ""
    svg.append(f'<line class="{cls}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"{m}/>')


def path(d, cls, arrow=False):
    m = ' marker-end="url(#arrow)"' if arrow else ""
    svg.append(f'<path class="{cls}" d="{d}"{m}/>')


def dot(x, y, cls="dot"):
    svg.append(f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="2.2"/>')


def badge(cx, cy, n):
    svg.append(f'<circle class="badge" cx="{cx:.1f}" cy="{cy:.1f}" r="11"/>')
    text(cx, cy, n, "badge-t", anchor="middle")


def chip_w(label: str) -> float:
    return tw(label, CHIP_FS) + 2 * CHIP_PAD


def chips_layout(items, max_w):
    out, cx, cy = [], 0.0, 0.0
    for label, kind in items:
        w = chip_w(label)
        if cx > 0 and cx + w > max_w:
            cx, cy = 0.0, cy + CHIP_H + 4
        if w > max_w:
            warnings.append(f"標籤比欄寬還寬 {w:.0f}>{max_w:.0f}: {label}")
        out.append((cx, cy, w, label, kind))
        cx += w + CHIP_GAP
    return out, (cy + CHIP_H if items else 0)


def chips_draw(x, y, items, max_w):
    layout, h = chips_layout(items, max_w)
    for dx, dy, w, label, kind in layout:
        svg.append(
            f'<g class="chip k-{kind}"><rect x="{x + dx:.1f}" y="{y + dy:.1f}" width="{w:.1f}" '
            f'height="{CHIP_H}" rx="9"/><text x="{x + dx + w / 2:.1f}" y="{y + dy + CHIP_H / 2:.1f}" '
            f'text-anchor="middle" dominant-baseline="central">{esc(label)}</text></g>'
        )
    return h


def chips_width(items) -> float:
    return sum(chip_w(label) for label, _ in items) + CHIP_GAP * (len(items) - 1)


def ref_lines(r: Ref) -> list[str]:
    """一個 Ref 在 SVG 右欄佔幾行。以「　」開頭的是接續行。"""
    head = (f"{r.who} · " if r.who else "") + r.path
    note = f"（{r.note}）" if r.note else ""
    one = f"{head} · {r.funcs}{note}" if r.funcs else f"{head}{note}"
    if tw(one, 11, mono=True) <= NOTE_MAX:
        return [one]
    if r.funcs and note and tw(f"{head} · {r.funcs}", 11, mono=True) <= NOTE_MAX:
        return [f"{head} · {r.funcs}", f"　{note}"]
    return [head, f"　{r.funcs}{note}" if r.funcs else f"　{note}"]


def refs_lines(refs) -> list[str]:
    out: list[str] = []
    for r in refs:
        out.extend(ref_lines(r))
    return out


def lines_h(lines) -> float:
    return len(lines) * NLH if lines else 0


def notes_draw(y_center_first, lines, where):
    y = y_center_first
    for s in lines:
        cont = s.startswith("　")
        if not cont:
            dot(NOTE_DOT_X, y, "ndot")
        text(
            NOTE_X,
            y,
            s,
            "code2" if cont else "code",
            max_w=NOTE_MAX,
            where=where,
            mono=True,
            size=11,
        )
        y += NLH


def notes_panel(y, h):
    rect(NX, y, NW, h, "npanel", rx=8)


def std_block(y, step: Step, box_w=FW):
    pad_t = 14
    title_c = y + pad_t + 9
    cur = y + pad_t + 18 + 10
    placed = []
    for a in step.acts:
        lines = refs_lines(a.refs)
        h = max(LH, lines_h(lines) + 3)
        placed.append((cur, a, lines))
        cur += h + 3
    max_chip_w = box_w - (TEXT_X - X0) - 14
    _, ch = chips_layout(step.chips, max_chip_w)
    chips_top = cur + 6
    bottom = chips_top + ch + 14

    rect(X0, y, box_w, bottom - y, "box")
    first = placed[0][0] - 8
    notes_panel(first, bottom - first)
    badge(X0 + 20, title_c, step.n)
    title = step.title + (f"（{step.tag}）" if step.tag else "")
    text(X0 + 40, title_c, title, "th", max_w=box_w - 54, where=title, size=14)
    for top, a, lines in placed:
        c = top + LH / 2
        dot(DOT_X, c)
        text(TEXT_X, c, a.text, "ts", max_w=box_w - (TEXT_X - X0) - 14, where=step.title)
        if lines:
            notes_draw(c, lines, step.title)
    chips_draw(TEXT_X, chips_top, step.chips, max_chip_w)
    return bottom


def arrow_down(y1, y2, label=""):
    line(SPINE, y1, SPINE, y2 - 8, "arr", arrow=True)
    if label:
        text(SPINE + 12, (y1 + y2) / 2, label, "ts", max_w=FW - 210, where="箭頭")


def draw_routing(y):
    pad_t = 14
    title_c = y + pad_t + 9
    head_top = y + pad_t + 18 + 10
    head_c = head_top + LH / 2
    cols_top = head_top + LH + 14
    inner_x = X0 + 14
    total = sum(t.width for t in TILES) + 9 * (len(TILES) - 1)
    if inner_x + total > X0 + FW - 14:
        warnings.append(f"路由五欄太寬 {total}")

    def body_lines(t: Tile) -> list[str]:
        out: list[str] = []
        for b in t.body:
            out.extend(b.lines)
        return out

    def col_h(t: Tile) -> float:
        h = 10 + LH + (16 if t.sub else 0) + 6 + len(body_lines(t)) * 16
        if t.chips:
            h += 6 + chips_layout(t.chips, t.width - 16)[1]
        if t.after:
            h += 6 + len(t.after.lines) * 15
        return h + 10

    cols_h = max(col_h(t) for t in TILES)
    tile_lines: list[str] = []
    for t in TILES:
        tile_lines.extend(refs_lines(replace(r, who=t.code) for r in t.refs))
    bottom = max(cols_top + cols_h + 14, cols_top + lines_h(tile_lines) + 14)

    rect(X0, y, FW, bottom - y, "box")
    notes_panel(head_top - 8, bottom - head_top + 8)
    badge(X0 + 20, title_c, "3")
    text(X0 + 40, title_c, ROUTE_TITLE, "th", max_w=FW - 54, where="b3", size=14)
    dot(DOT_X, head_c)
    text(TEXT_X, head_c, ROUTE_ACT.text, "ts", max_w=FW - 68, where="b3")
    notes_draw(head_c, refs_lines(ROUTE_ACT.refs), "b3")
    notes_draw(cols_top + 8, tile_lines, "b3 tiles")

    cx = inner_x
    for t in TILES:
        rect(cx, cols_top, t.width, cols_h, "cell dash" if t.stub else "cell", rx=6)
        yy = cols_top + 10 + LH / 2
        head = f"{t.code}　{t.name}" if t.name else t.code
        text(cx + 8, yy, head, "ch", max_w=t.width - 16, where=head, size=12.5)
        yy += LH / 2
        if t.sub:
            text(cx + 8, yy + 8, t.sub, "tsm", max_w=t.width - 16, where=head, size=11)
            yy += 16
        yy += 6
        for s in body_lines(t):
            text(
                cx + 8,
                yy + 8,
                s,
                "tf" if t.stub else "tc",
                max_w=t.width - 16,
                where=head,
                size=11.5,
            )
            yy += 16
        if t.chips:
            yy += 6
            yy += chips_draw(cx + 8, yy, t.chips, t.width - 16)
        if t.after:
            yy += 6
            for s in t.after.lines:
                text(cx + 8, yy + 7.5, s, "tf", max_w=t.width - 16, where=head, size=11)
                yy += 15
        cx += t.width + 9
    return bottom


def draw_step4(y):
    main_w = 390
    top = y
    bottom = std_block(y, STEP4, box_w=main_w)
    bx = X0 + main_w + 36
    bw = FW - main_w - 36
    bh = 52
    mid = (top + bottom) / 2
    for i, (t1, t2) in enumerate(BRANCHES):
        by = mid - bh - 6 if i == 0 else mid + 6
        rect(bx, by, bw, bh, "box alt")
        text(bx + 12, by + 17, t1, "th2", max_w=bw - 24, where=t1, size=13)
        text(bx + 12, by + 36, t2, "tc", max_w=bw - 24, where=t1, size=11.5)
        path(
            f"M{X0 + main_w:.1f} {mid:.1f} H{X0 + main_w + 14:.1f} V{by + bh / 2:.1f} H{bx - 8:.1f}",
            "arr",
            arrow=True,
        )
    return bottom


def draw_agent(y):
    lab_x = X0 + 12
    lab_w = 84
    cell_x0 = lab_x + lab_w + 8
    cw = (X0 + FW - 12 - cell_x0 - 16) / 3
    cell_xs = [cell_x0 + i * (cw + 8) for i in range(3)]

    def geom(cell: Cell, xi: int):
        return cell_xs[xi], cw * cell.span + 8 * (cell.span - 1)

    def measure(cell: Cell, w: float):
        inner = w - 16
        lines = cell.lines.lines
        inline = False
        if len(lines) == 1 and cell.chips:
            inline = tw(lines[0], TC) + 12 + chips_width(cell.chips) <= inner
        h = 8 + len(lines) * TCL
        if cell.chips and not inline:
            h += 5 + chips_layout(cell.chips, inner)[1]
        if inline:
            h = max(h, 8 + CHIP_H)
        if cell.small:
            h += 4 + len(cell.small.lines) * 15
        return h + 8, inline

    def draw_cell(cell: Cell, x, cy, w, h, inline):
        cls = {"normal": "cell", "stub": "cell dash", "skip": "cell skip"}[cell.kind]
        rect(x, cy, w, h, cls, rx=6)
        inner = w - 16
        yy = cy + 8
        tcls = "tc" if cell.kind == "normal" else "tf"
        for s in cell.lines.lines:
            text(x + 8, yy + TCL / 2, s, tcls, max_w=inner, where="grid", size=TC)
            yy += TCL
        if cell.chips:
            if inline:
                chips_draw(
                    x + 8 + tw(cell.lines.lines[0], TC) + 12,
                    cy + 8 + (TCL - CHIP_H) / 2,
                    cell.chips,
                    999,
                )
            else:
                yy += 5
                yy += chips_draw(x + 8, yy, cell.chips, inner)
        if cell.small:
            yy += 4
            for s in cell.small.lines:
                text(x + 8, yy + 7.5, s, "tf", max_w=inner, where="grid small", size=11)
                yy += 15

    pad_t = 14
    title_c = y + pad_t + 9
    sub_top = y + pad_t + 18 + 10
    sub_rows = []
    cur = sub_top
    for a in AGENT_ACTS:
        lines = refs_lines(a.refs)
        h = max(LH, lines_h(lines) + 3)
        sub_rows.append((cur, a, lines))
        cur += h + 3
    head_top = cur + 8
    head_h = 26
    grid_top = head_top + head_h + 8

    specs = []
    gy = grid_top
    for row in AGENT_ROWS:
        lines = refs_lines(row.refs)
        row_h = max(len(row.label) * 16 + 16, lines_h(lines) + 14)
        measured = []
        xi = 0
        for cell in row.cells:
            x, w = geom(cell, xi)
            h, inline = measure(cell, w)
            measured.append((cell, x, w, inline))
            row_h = max(row_h, h)
            xi += cell.span
        specs.append((gy, row_h, row, measured, lines))
        gy += row_h + 8
    bottom = gy + 6

    rect(X0, y, FW, bottom - y, "frame", rx=10)
    notes_panel(sub_top - 8, bottom - sub_top + 8)
    badge(X0 + 20, title_c, "5")
    text(X0 + 40, title_c, AGENT_TITLE, "th agent-t", max_w=FW - 54, where="b5", size=14)
    for top, a, lines in sub_rows:
        c = top + LH / 2
        dot(DOT_X, c)
        text(TEXT_X, c, a.text, "ts", max_w=FW - 68, where="b5 sub")
        if lines:
            notes_draw(c, lines, "b5 sub")

    rect(lab_x, head_top, lab_w, head_h, "ghead", rx=5)
    text(lab_x + 8, head_top + head_h / 2, "步驟", "gh", size=12)
    for i, h1 in enumerate(AGENT_COLS):
        rect(cell_xs[i], head_top, cw, head_h, "ghead", rx=5)
        text(
            cell_xs[i] + 8,
            head_top + head_h / 2,
            h1,
            "gh",
            max_w=cw - 16,
            where="grid head",
            size=12,
        )

    for gy, row_h, row, measured, lines in specs:
        for i, s in enumerate(row.label):
            text(
                lab_x + 8,
                gy + 16 + i * 16,
                s,
                "gl" if i == 0 else "tsm",
                max_w=lab_w - 10,
                where="label",
                size=12.5 if i == 0 else 11,
            )
        for cell, x, w, inline in measured:
            draw_cell(cell, x, gy, w, row_h, inline)
        notes_draw(gy + 16, lines, "grid notes " + row.label[0])
        line(NX + 10, gy + row_h + 4, NX + NW - 10, gy + row_h + 4, "sep")
    return bottom


def draw_footer(y):
    fx = X0 - 24
    fw = NX + NW - fx
    h = 16 + 22 + 19 * len(FOOT) + 10
    rect(fx, y, fw, h, "npanel", rx=8)
    yy = y + 16
    text(fx + 16, yy + 6, FOOT_TITLE, "nh", size=12.5)
    yy += 22
    for kind, s in FOOT:
        if kind == "b":
            dot(fx + 20, yy + 6, "ndot")
        text(fx + 30, yy + 6, s, "ts", max_w=fw - 46, where="foot")
        yy += 19
    return y + h


SVG_STYLE = """
text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif}
.bg{fill:#FFFFFF}
.title{font-size:17px;font-weight:600;fill:#2C2C2A}
.th{font-size:14px;font-weight:500;fill:#2C2C2A}
.th2{font-size:13px;font-weight:500;fill:#2C2C2A}
.ts{font-size:12px;fill:#5F5E5A}
.tsm{font-size:11px;fill:#5F5E5A}
.tc{font-size:11.5px;fill:#2C2C2A}
.tf{font-size:11px;fill:#888780}
.ch{font-size:12.5px;font-weight:600;fill:#2C2C2A}
.gh{font-size:12px;font-weight:600;fill:#3C3489}
.gl{font-size:12.5px;font-weight:600;fill:#2C2C2A}
.nh{font-size:12.5px;font-weight:600;fill:#2C2C2A}
.code,.code2{font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:11px}
.code{fill:#2C2C2A}
.code2{fill:#5F5E5A}
.box{fill:#FFFFFF;stroke:#B4B2A9;stroke-width:0.8}
.box.alt{fill:#FBFAF7}
.dash{stroke-dasharray:4 3}
.cell{fill:#FFFFFF;stroke:#D3D1C7;stroke-width:0.8}
.cell.skip{fill:none;stroke:#D3D1C7;stroke-dasharray:2 3}
.frame{fill:#F7F6FE;stroke:#534AB7;stroke-width:1}
.agent-t{fill:#3C3489}
.ghead{fill:#EEEDFE;stroke:none}
.npanel{fill:#F7F6F2;stroke:#E3E1D8;stroke-width:0.8}
.sep{stroke:#E3E1D8;stroke-width:0.8}
.arr{fill:none;stroke:#888780;stroke-width:1.5}
.head{fill:none;stroke:#888780}
.dot{fill:#888780}
.ndot{fill:#B4B2A9}
.badge{fill:#2C2C2A}
.badge-t{font-size:12px;font-weight:600;fill:#FFFFFF}
.chip>text{font-size:11px;font-weight:500}
.k-gpu>rect{fill:#FAECE7;stroke:#D85A30;stroke-width:0.9}
.k-gpu>text{fill:#712B13}
.k-cpu>rect{fill:#E1F5EE;stroke:#1D9E75;stroke-width:0.9}
.k-cpu>text{fill:#085041}
.k-none>rect{fill:#F1EFE8;stroke:#B4B2A9;stroke-width:0.8}
.k-none>text{fill:#5F5E5A}
@media (prefers-color-scheme:dark){
.bg{fill:#1C1C1B}
.title,.th,.th2,.tc,.ch,.gl,.nh,.code{fill:#F1EFE8}
.ts,.tsm,.code2{fill:#B4B2A9}
.tf{fill:#888780}
.gh,.agent-t{fill:#CECBF6}
.box{fill:#1C1C1B;stroke:#888780}
.box.alt{fill:#232321}
.cell{fill:#1C1C1B;stroke:#444441}
.cell.skip{fill:none;stroke:#444441}
.frame{fill:#1F1D33;stroke:#AFA9EC}
.ghead{fill:#2E2A55}
.npanel{fill:#242422;stroke:#3A3A37}
.sep{stroke:#3A3A37}
.arr,.head{stroke:#B4B2A9}
.dot{fill:#B4B2A9}
.ndot{fill:#5F5E5A}
.badge{fill:#F1EFE8}
.badge-t{fill:#1C1C1B}
.k-gpu>rect{fill:#4A1B0C;stroke:#F0997B}
.k-gpu>text{fill:#F5C4B3}
.k-cpu>rect{fill:#04342C;stroke:#5DCAA5}
.k-cpu>text{fill:#9FE1CB}
.k-none>rect{fill:#2C2C2A;stroke:#5F5E5A}
.k-none>text{fill:#B4B2A9}
}
"""


def build_svg() -> str:
    svg.clear()
    text(X0 - 24, 34, SVG_TITLE, "title", max_w=W - 56, where="title", size=17)
    text(X0 - 24, 58, STATUS, "ts", max_w=W - 56, where="status")
    text(X0 - 24, 78, SOURCE, "ts", max_w=W - 56, where="source")

    ly = 108
    lx = X0 - 24
    for items, desc in LEGEND:
        chips_draw(lx, ly - CHIP_H / 2, items, 400)
        lx += chips_width(items) + 8
        text(lx, ly, desc, "ts")
        lx += tw(desc, 12) + 26
    rect(lx, ly - 8, 22, 16, "box dash", rx=4)
    text(lx + 30, ly, "尚未建立／空殼", "ts")
    if lx + 30 + tw("尚未建立／空殼", 12) > W - 24:
        warnings.append("圖例溢出")
    head = "右側：每個動作對應的程式碼"
    text(NX, 142, head, "nh", size=12.5)
    text(
        NX + tw(head, 12.5) + 10,
        142,
        "modules/、shared/ 在 packages/ 裡",
        "ts",
        max_w=NW - tw(head, 12.5) - 10,
        where="notes header",
    )

    y = 160.0
    b0 = std_block(y, STEP0)
    arrow_down(b0, b0 + GAP, STEP0.edge)
    y1 = b0 + GAP
    b1 = std_block(y1, STEP1)
    arrow_down(b1, b1 + GAP)
    y2 = b1 + GAP
    b2 = std_block(y2, STEP2)
    lane = X0 - 24
    path(f"M{X0:.1f} {y2 + 32:.1f} H{lane:.1f} V{y1 + 32:.1f} H{X0 - 8:.1f}", "arr", arrow=True)
    text(X0 + 10, (b1 + y2) / 2, "↻ " + LOOP, "ts", max_w=SPINE - X0 - 18, where="loop")
    arrow_down(b2, b2 + GAP, STEP2.edge)
    b3 = draw_routing(b2 + GAP)
    arrow_down(b3, b3 + GAP, ROUTE_EDGE)
    b4 = draw_step4(b3 + GAP)
    arrow_down(b4, b4 + GAP, STEP4.edge)
    b5 = draw_agent(b4 + GAP)
    arrow_down(b5, b5 + GAP, AGENT_EDGE)
    b6 = std_block(b5 + GAP, STEP6)
    end = draw_footer(b6 + 28)
    height = end + 24

    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height:.0f}" '
        f'viewBox="0 0 {W} {height:.0f}" role="img">\n'
        f"<title>{esc(SVG_TITLE)}（{VERIFIED}）</title>\n"
        "<desc>專題主要架構。從開機、使用者輸入、對話層追問、A 到 E 五個模組的路由打分、選出一個模組、"
        "該模組的 M5 Agent 流水線（截圖理解、合併、去識別化、分類、檢索、生成、合成），到輸出檢核與呈現。"
        "每一步標出呼叫的模型、跑在 CPU 或 GPU、放在記憶體或顯卡記憶體；右側列出每個動作對應的程式碼檔案與函式。"
        f"由 tools/gen_main_architecture.py 產生。</desc>\n"
        f"<style>{SVG_STYLE}</style>\n"
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
        'orient="auto-start-reverse"><path class="head" d="M2 1L8 5L2 9" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"/></marker></defs>\n'
        f'<rect class="bg" width="{W}" height="{height:.0f}" rx="12"/>\n'
    )
    return head + "\n".join(svg) + "\n</svg>\n"


# ══════════════════════════════════════════════════════════════
#  網頁版
# ══════════════════════════════════════════════════════════════


def h(s: str) -> str:
    return html.escape(s, quote=True)


def chip_html(label: str, kind: str) -> str:
    return f'<span class="chip k-{kind}">{h(label)}</span>'


def chips_html(items, cls="chips") -> str:
    if not items:
        return ""
    return f'<div class="{cls}">' + "".join(chip_html(*c) for c in items) + "</div>"


def ref_html(r: Ref, with_who: bool = True) -> str:
    who = f'<span class="who">{h(r.who)}</span>' if (r.who and with_who) else ""
    fn = f' <code class="fn">{h(r.funcs)}</code>' if r.funcs else ""
    note = f' <span class="rnote">{h(r.note)}</span>' if r.note else ""
    link = f'<a href="{h(r.url)}" target="_blank" rel="noopener"><code>{h(r.path)}</code></a>'
    return f"<li>{who}{link}{fn}{note}</li>"


def refs_html(refs, cls="refs", with_who=True) -> str:
    if not refs:
        return f'<div class="{cls} is-empty"></div>'
    return f'<ul class="{cls}">' + "".join(ref_html(r, with_who) for r in refs) + "</ul>"


def rows_html(acts) -> str:
    rows = []
    for a in acts:
        rows.append(f'<div class="row"><p class="act">{h(a.text)}</p>{refs_html(a.refs)}</div>')
    return '<div class="rows">' + "".join(rows) + "</div>"


def head_html(n: str, title: str, tag: str = "", cls: str = "") -> str:
    t = f'<span class="tag">{h(tag)}</span>' if tag else ""
    return (
        f'<header class="step-head"><span class="node" aria-hidden="true">{h(n)}</span>'
        f'<h3 class="{cls}"><span class="vh">步驟 {h(n)}：</span>{h(title)}</h3>{t}</header>'
    )


def edge_html(label: str = "", loop: str = "") -> str:
    lab = f'<span class="edge-label">{h(label)}</span>' if label else ""
    lp = f'<span class="loop">↻ {h(loop)}</span>' if loop else ""
    return f'<div class="edge">{lab}{lp}</div>'


def step_html(step: Step, extra: str = "") -> str:
    return (
        f'<section class="step" id="step-{step.n}"><div class="card">'
        f"{head_html(step.n, step.title, step.tag)}{rows_html(step.acts)}{extra}"
        f"{chips_html(step.chips)}</div></section>"
    )


def routing_html() -> str:
    tiles = []
    for t in TILES:
        head = (
            f'<div class="tile-head"><b>{h(t.code)}</b>{(" " + h(t.name)) if t.name else ""}</div>'
        )
        sub = f'<div class="tile-sub">{h(t.sub)}</div>' if t.sub else ""
        body = "".join(f"<p>{h(b.joined)}</p>" for b in t.body)
        after = f'<p class="faint">{h(t.after.joined)}</p>' if t.after else ""
        cls = "tile is-stub" if t.stub else "tile"
        tiles.append(
            f'<div class="{cls}">{head}{sub}{body}{chips_html(t.chips)}{after}'
            f"{refs_html(t.refs, 'refs tile-refs', with_who=False)}</div>"
        )
    return (
        '<section class="step" id="step-3"><div class="card">'
        f"{head_html('3', ROUTE_TITLE)}{rows_html((ROUTE_ACT,))}"
        f'<div class="tiles">{"".join(tiles)}</div></div></section>'
    )


def step4_html() -> str:
    branches = "".join(
        f'<div class="branch"><b>{h(t1)}</b><span>{h(t2)}</span></div>' for t1, t2 in BRANCHES
    )
    extra = f'<div class="branches"><p class="branches-label">兩條分支，不走 ⑤</p>{branches}</div>'
    return step_html(STEP4, extra)


def agent_html() -> str:
    parts = ['<div class="gh">步驟</div>'] + [f'<div class="gh">{h(c)}</div>' for c in AGENT_COLS]
    for i, row in enumerate(AGENT_ROWS):
        r = 2 + 2 * i
        label = f"<b>{h(row.label[0])}</b>" + "".join(f"<span>{h(x)}</span>" for x in row.label[1:])
        parts.append(f'<div class="gl" style="grid-row:{r} / span 2">{label}</div>')
        col = 2
        for cell in row.cells:
            small = f'<p class="small">{h(cell.small.joined)}</p>' if cell.small else ""
            kind = {"normal": "cell", "stub": "cell is-stub", "skip": "cell is-skip"}[cell.kind]
            parts.append(
                f'<div class="{kind}" style="grid-row:{r};grid-column:{col} / span {cell.span}">'
                f"<p>{h(cell.lines.joined)}</p>{chips_html(cell.chips)}{small}</div>"
            )
            col += cell.span
        refs = "".join(ref_html(x) for x in row.refs)
        parts.append(
            f'<ul class="refs grid-refs" style="grid-row:{r + 1};grid-column:2 / -1">{refs}</ul>'
        )
    grid = "".join(parts)
    return (
        '<section class="step" id="step-5"><div class="card agent">'
        f"{head_html('5', AGENT_TITLE, cls='agent-title')}{rows_html(AGENT_ACTS)}"
        '<div class="grid-wrap" role="region" aria-label="M5 Agent 的各個步驟，A、C、E 三個模組對照（可左右捲動）" tabindex="0">'
        f'<div class="agrid">{grid}</div></div></div></section>'
    )


MEMORY_SVG = """
<svg class="mm" viewBox="0 0 460 392" role="img" aria-label="{label}">
  <defs><marker id="mm-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="mm-head" d="M2 1L8 5L2 9"/></marker></defs>
  <rect class="mm-proc mm-cpu" x="6" y="6" width="448" height="210" rx="12"/>
  <text class="mm-title" x="24" y="34">Streamlit 的 Python 行程</text>
  <rect class="mm-badge mm-cpu-b" x="330" y="21" width="108" height="24" rx="12"/>
  <text class="mm-badge-t mm-cpu-t" x="384" y="33" text-anchor="middle">CPU・記憶體</text>
  <rect class="mm-item" x="22" y="54" width="416" height="44" rx="8"/>
  <text class="mm-name" x="36" y="71">bge-m3（fp32）</text>
  <text class="mm-sub" x="36" y="88">約 2.2 GB　開機時背景載入，一直留著</text>
  <rect class="mm-item" x="22" y="106" width="416" height="44" rx="8"/>
  <text class="mm-name" x="36" y="123">OCR：PP-OCRv6 small</text>
  <text class="mm-sub" x="36" y="140">第一張截圖才載入；跑久了約 1.5 GB</text>
  <rect class="mm-item mm-dash" x="22" y="158" width="416" height="44" rx="8"/>
  <text class="mm-name" x="36" y="175">索引＋語料（A 73 MB、C 64 MB）</text>
  <text class="mm-sub" x="36" y="192">每次查詢從磁碟讀進來，用完就放掉</text>
  <line class="mm-arrow" x1="230" y1="218" x2="230" y2="272" marker-end="url(#mm-arrow)"/>
  <text class="mm-sub" x="244" y="240">call_slm() 經 HTTP</text>
  <text class="mm-code" x="244" y="257">127.0.0.1:11434</text>
  <text class="mm-sub" x="216" y="248" text-anchor="end">C 的生成、E 的空呼叫</text>
  <rect class="mm-proc mm-gpu" x="6" y="280" width="448" height="104" rx="12"/>
  <text class="mm-title" x="24" y="308">Ollama 行程</text>
  <rect class="mm-badge mm-gpu-b" x="316" y="295" width="122" height="24" rx="12"/>
  <text class="mm-badge-t mm-gpu-t" x="377" y="307" text-anchor="middle">GPU・顯卡記憶體</text>
  <rect class="mm-item" x="22" y="326" width="416" height="44" rx="8"/>
  <text class="mm-name" x="36" y="343">qwen2.5:3b（Q4_K_M）</text>
  <text class="mm-sub" x="36" y="360">約 2.4 GB　第一次呼叫才載入，閒置 5 分鐘卸載</text>
</svg>
"""
MEMORY_CAPTION = (
    "bge-m3 與 OCR 跟介面在同一個 Python 行程裡，放在系統記憶體、用 CPU 算；只有 qwen2.5:3b 在 Ollama 的行程裡，"
    "放在顯卡記憶體、用 GPU 算。兩邊只靠一條本機 HTTP 連線溝通。Mac 沒有獨立的顯卡記憶體，GPU 用的是統一記憶體。"
)


def summary_html() -> str:
    rows = []
    for m in MODELS:
        rows.append(
            "<tr>"
            f'<th scope="row"><span class="m-name">{h(m["name"])}</span><span class="m-sub">{h(m["sub"])}</span></th>'
            f"<td>{h(m['where'])}</td>"
            f'<td><div class="stack">{chip_html(*m["device"])}{chip_html(*m["mem"])}</div></td>'
            f'<td class="proof">{h(m["proof"])}</td>'
            "</tr>"
        )
    table = (
        '<div class="table-wrap"><table class="models">'
        '<colgroup><col style="width:23%"><col style="width:31%"><col style="width:26%">'
        '<col style="width:20%"></colgroup><thead><tr>'
        '<th scope="col">模型</th><th scope="col">在哪一步</th><th scope="col">運算・放在哪</th>'
        '<th scope="col">實測</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )
    fig = (
        f'<figure class="mm-fig">{MEMORY_SVG.format(label=h(MEMORY_CAPTION))}'
        f"<figcaption>{h(MEMORY_CAPTION)}</figcaption></figure>"
    )
    return (
        '<section class="summary" aria-labelledby="sum-title">'
        '<h2 id="sum-title">整條流程只用到三個模型</h2>'
        '<p class="sec-lede">其餘步驟都是規則或詞表比對，沒有模型、全在 CPU。</p>'
        f'<div class="summary-grid">{fig}<div>{table}</div></div></section>'
    )


def header_html() -> str:
    legend = []
    for items, desc in LEGEND:
        legend.append(
            f'<span class="lg">{"".join(chip_html(*c) for c in items)}<span>{h(desc)}</span></span>'
        )
    legend.append(
        '<span class="lg"><span class="lg-stub" aria-hidden="true"></span><span>尚未建立／空殼</span></span>'
    )
    return (
        '<header class="top">'
        f'<p class="eyebrow">專題主要架構・{VERIFIED} 定案</p>'
        f"<h1>{h(TITLE)}</h1>"
        f'<p class="lede">{h(LEDE)}</p>'
        f'<p class="meta">現況：{h(BRANCH)} 分支 <code>{h(COMMIT)}</code> ・ {h(VERIFIED)} 在 A 的 Mac（M5）實跑確認'
        " ・ 其他流程圖已列為廢案</p>"
        f'<div class="legend">{"".join(legend)}</div></header>'
    )


def flow_html() -> str:
    return (
        '<section class="flow" aria-labelledby="flow-title">'
        '<h2 id="flow-title">產品流程</h2>'
        '<p class="sec-lede">由上往下是執行順序。每張卡片左邊是動作、右邊是對應的程式碼（點檔名會開 GitHub 的 '
        f"{h(BRANCH)} 分支）；卡片底下的標籤是這一步呼叫的模型、跑在哪、放在哪。</p>"
        f"{step_html(STEP0)}{edge_html(STEP0.edge)}"
        f"{step_html(STEP1)}{edge_html(loop=LOOP)}"
        f"{step_html(STEP2)}{edge_html(STEP2.edge)}"
        f"{routing_html()}{edge_html(ROUTE_EDGE)}"
        f"{step4_html()}{edge_html(STEP4.edge)}"
        f"{agent_html()}{edge_html(AGENT_EDGE)}"
        f"{step_html(STEP6)}"
        "</section>"
    )


def details_html() -> str:
    bullets: list[str] = []
    for kind, s in FOOT:
        if kind == "b" or not bullets:
            bullets.append(s)
        else:
            bullets[-1] += s
    items = "".join(f"<li>{h(b)}</li>" for b in bullets)
    return (
        '<section class="details" aria-labelledby="det-title">'
        f'<h2 id="det-title">{h(FOOT_TITLE)}</h2><ul class="facts">{items}</ul>'
        '<h2 id="src-title">這張圖怎麼來的</h2><ul class="facts">'
        f"<li>{h(VERIFIED)} 在 A 的 Mac（M5）實跑三次：對話分頁交給 C、單次查詢附截圖、單次查詢交給 E。"
        "步驟順序出自執行紀錄；跑在哪裡出自 <code>ollama ps</code>、onnxruntime 的 providers、"
        "bge-m3 參數的 device。</li>"
        "<li>GitHub 上看的圖是 <code>docs/img/main-architecture.svg</code>，這一頁是 "
        "<code>docs/main-architecture.html</code>，兩個都由 <code>tools/gen_main_architecture.py</code> "
        "從同一份內容產生；程式改了就改產生器裡的內容再重跑。</li>"
        f"<li>其他流程圖已於 {h(VERIFIED)} 列為廢案，原封不動保存在 <code>docs/廢案/</code>。</li>"
        "</ul></section>"
    )


CSS = """
:root{
  --ground:#F4F6F5;--surface:#FFFFFF;--ink:#1C2220;--ink-2:#55605B;--ink-3:#838D88;
  --line:#D5DCD8;--line-2:#E6EBE8;--panel:#EDF1EF;
  --gpu-bg:#FCECE4;--gpu-line:#CF5327;--gpu-ink:#7C2A0F;
  --cpu-bg:#E0F3EC;--cpu-line:#168C6A;--cpu-ink:#0A5440;
  --none-bg:#ECEFED;--none-line:#B7C0BB;--none-ink:#55605B;
  --agent:#4B44AE;--agent-bg:#F5F4FD;--agent-head:#E8E6FA;
  --node:#1C2220;--node-ink:#FFFFFF;--link:#0E6A8C;
  --sans:"IBM Plex Sans","Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,Consolas,"Noto Sans TC",monospace;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --ground:#111513;--surface:#1A1F1D;--ink:#E6EBE8;--ink-2:#A5AFAA;--ink-3:#79837E;
    --line:#2F3734;--line-2:#262D2A;--panel:#151A18;
    --gpu-bg:#3B1A0D;--gpu-line:#EE8C63;--gpu-ink:#F6C6B0;
    --cpu-bg:#0B2D24;--cpu-line:#4DC4A0;--cpu-ink:#A4E5D1;
    --none-bg:#242A27;--none-line:#56605B;--none-ink:#A5AFAA;
    --agent:#ABA5F2;--agent-bg:#1B1A2D;--agent-head:#292748;
    --node:#E6EBE8;--node-ink:#111513;--link:#7CC7E8;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --ground:#111513;--surface:#1A1F1D;--ink:#E6EBE8;--ink-2:#A5AFAA;--ink-3:#79837E;
  --line:#2F3734;--line-2:#262D2A;--panel:#151A18;
  --gpu-bg:#3B1A0D;--gpu-line:#EE8C63;--gpu-ink:#F6C6B0;
  --cpu-bg:#0B2D24;--cpu-line:#4DC4A0;--cpu-ink:#A4E5D1;
  --none-bg:#242A27;--none-line:#56605B;--none-ink:#A5AFAA;
  --agent:#ABA5F2;--agent-bg:#1B1A2D;--agent-head:#292748;
  --node:#E6EBE8;--node-ink:#111513;--link:#7CC7E8;
}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font:14px/1.6 var(--sans)}
.page{max-width:1120px;margin:0 auto;padding-inline:16px;padding-block:32px 56px}
h1,h2,h3{text-wrap:balance}
code{font-family:var(--mono);font-size:.92em}
a{color:var(--link)}
a:focus-visible,.grid-wrap:focus-visible{outline:2px solid var(--link);outline-offset:2px;border-radius:4px}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}

.top{padding-bottom:22px;border-bottom:1px solid var(--line);margin-bottom:28px}
.eyebrow{margin:0 0 6px;font-size:12px;letter-spacing:.08em;color:var(--ink-2)}
h1{margin:0;font-size:30px;line-height:1.25;font-weight:600;letter-spacing:.01em}
.lede{margin:10px 0 0;font-size:15.5px;color:var(--ink-2);max-width:48em}
.meta{margin:8px 0 0;font-size:12.5px;color:var(--ink-3)}
.legend{display:flex;flex-wrap:wrap;gap:10px 22px;margin-top:16px;font-size:12.5px;color:var(--ink-2)}
.lg{display:inline-flex;align-items:center;gap:6px}
.lg-stub{display:inline-block;width:22px;height:14px;border:1px dashed var(--ink-3);border-radius:4px}

h2{margin:0 0 4px;font-size:19px;font-weight:600}
.sec-lede{margin:0 0 16px;color:var(--ink-2);max-width:60em}

.chip{display:inline-flex;align-items:center;font:500 12px/1 var(--sans);padding:4px 9px;border-radius:999px;border:1px solid;white-space:nowrap}
.k-gpu{background:var(--gpu-bg);border-color:var(--gpu-line);color:var(--gpu-ink)}
.k-cpu{background:var(--cpu-bg);border-color:var(--cpu-line);color:var(--cpu-ink)}
.k-none{background:var(--none-bg);border-color:var(--none-line);color:var(--none-ink)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}

.summary{margin-bottom:36px}
.summary-grid{display:grid;grid-template-columns:minmax(0,400px) minmax(0,1fr);gap:24px;align-items:start}
.mm-fig{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px}
.mm{display:block;width:100%;height:auto;max-width:100%}
.mm figcaption,.mm-fig figcaption{margin-top:10px;font-size:12.5px;color:var(--ink-2)}
.mm-proc{stroke-width:1.2}
.mm-cpu{fill:var(--cpu-bg);stroke:var(--cpu-line)}
.mm-gpu{fill:var(--gpu-bg);stroke:var(--gpu-line)}
.mm-item{fill:var(--surface);stroke:var(--line);stroke-width:1}
.mm-dash{stroke-dasharray:4 3}
.mm-badge{stroke-width:1}
.mm-cpu-b{fill:var(--surface);stroke:var(--cpu-line)}
.mm-gpu-b{fill:var(--surface);stroke:var(--gpu-line)}
.mm-badge-t{font:600 11.5px var(--sans);dominant-baseline:central}
.mm-cpu-t{fill:var(--cpu-ink)}
.mm-gpu-t{fill:var(--gpu-ink)}
.mm-title{font:600 14px var(--sans);fill:var(--ink);dominant-baseline:central}
.mm-name{font:600 12.5px var(--sans);fill:var(--ink);dominant-baseline:central}
.mm-sub{font:12px var(--sans);fill:var(--ink-2);dominant-baseline:central}
.mm-code{font:12px var(--mono);fill:var(--ink);dominant-baseline:central}
.mm-arrow{stroke:var(--ink-2);stroke-width:1.6}
.mm-head{fill:none;stroke:var(--ink-2);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.table-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:12px}
.models{width:100%;min-width:34rem;border-collapse:collapse;table-layout:fixed;font-size:13px}
.models th,.models td{text-align:left;vertical-align:top;padding:10px 12px;border-bottom:1px solid var(--line-2)}
.models thead th{font-size:12px;font-weight:600;color:var(--ink-2);background:var(--panel)}
.models tbody tr:last-child th,.models tbody tr:last-child td{border-bottom:0}
.m-name{display:block;font-weight:600;font-family:var(--mono);font-size:13px}
.m-sub{display:block;font-weight:400;font-size:12px;color:var(--ink-3)}
.proof{color:var(--ink-2);overflow-wrap:anywhere}
.stack{display:flex;flex-direction:column;align-items:flex-start;gap:6px}
.models .chip{white-space:normal;line-height:1.3}

.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px 16px}
.step-head{display:flex;align-items:center;flex-wrap:wrap;gap:8px 10px;margin-bottom:10px}
.node{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;background:var(--node);color:var(--node-ink);font:600 12.5px/1 var(--sans);flex:none}
.step-head h3{margin:0;font-size:15.5px;font-weight:600}
.tag{font-size:12px;color:var(--ink-2);border:1px solid var(--line);border-radius:999px;padding:1px 9px}
.rows{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr)}
.row{display:contents}
.act{margin:0;padding:6px 14px 6px 34px;position:relative;color:var(--ink-2)}
.act::before{content:"";position:absolute;left:20px;top:15px;width:5px;height:5px;border-radius:50%;background:var(--ink-3)}
.refs{list-style:none;margin:0;padding:6px 12px;background:var(--panel);font-size:12.5px;line-height:1.55}
.refs li+li{margin-top:2px}
.row:first-child .refs{border-top-left-radius:8px;border-top-right-radius:8px}
.row:last-child .refs{border-bottom-left-radius:8px;border-bottom-right-radius:8px}
.refs a{text-decoration:none}
.refs a:hover code{text-decoration:underline}
.refs a code{color:var(--link)}
.fn{color:var(--ink)}
.rnote{color:var(--ink-3);font-size:12px}
.who{display:inline-block;min-width:1.5em;font-weight:600;color:var(--ink-2)}
.card>.chips{margin-left:34px}

.edge{position:relative;display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;min-height:40px;padding:8px 0 8px 52px;color:var(--ink-2);font-size:13px}
.edge::before{content:"";position:absolute;left:28px;top:0;bottom:9px;border-left:1.5px solid var(--ink-3)}
.edge::after{content:"";position:absolute;left:23.5px;bottom:2px;border:5.25px solid transparent;border-top:7px solid var(--ink-3);border-bottom:0}
.loop{font-size:12.5px;color:var(--ink-2);border:1px dashed var(--ink-3);border-radius:999px;padding:1px 10px}

.tiles{display:grid;grid-template-columns:1.5fr .7fr 1.1fr .7fr 1.1fr;gap:10px;margin-top:12px}
.tile{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--surface);min-width:0}
.tile.is-stub{border-style:dashed;background:transparent;color:var(--ink-3)}
.tile-head{font-weight:600;font-size:13.5px}
.tile-head b{margin-right:4px}
.tile-sub{font-size:12px;color:var(--ink-2)}
.tile p{margin:6px 0 0;font-size:13px}
.tile .faint{color:var(--ink-3);font-size:12.5px}
.tile .chips{margin-top:8px}
.tile-refs{background:none;padding:8px 0 0;margin-top:10px;border-top:1px dashed var(--line);font-size:12px;border-radius:0}

.branches{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0 0 34px}
.branches-label{grid-column:1 / -1;margin:0;font-size:12.5px;color:var(--ink-2)}
.branch{border:1px solid var(--line);border-radius:8px;padding:8px 12px;background:var(--panel)}
.branch b{display:block;font-size:13.5px}
.branch span{font-size:13px;color:var(--ink-2)}

.card.agent{background:var(--agent-bg);border-color:var(--agent)}
.agent-title{color:var(--agent)}
.grid-wrap{overflow-x:auto;margin-top:12px;padding-bottom:4px}
.agrid{display:grid;grid-template-columns:7rem repeat(3,minmax(11rem,1fr));gap:6px 8px;min-width:46rem}
.gh{background:var(--agent-head);color:var(--agent);font-weight:600;font-size:13px;padding:6px 10px;border-radius:6px}
.gl{padding:6px 4px;font-size:12.5px;color:var(--ink-2)}
.gl b{display:block;font-size:13.5px;color:var(--ink)}
.gl span{display:block}
.cell{background:var(--surface);border:1px solid var(--line);border-radius:7px;padding:8px 10px;font-size:13px;min-width:0}
.cell p{margin:0}
.cell .chips{margin-top:8px}
.cell .small{margin-top:6px;color:var(--ink-3);font-size:12px}
.cell.is-stub{border-style:dashed;background:transparent;color:var(--ink-3)}
.cell.is-skip{border:1px dotted var(--line);background:transparent;color:var(--ink-3)}
.grid-refs{display:flex;flex-wrap:wrap;gap:2px 18px;background:none;padding:0 2px 10px;margin-bottom:4px;border-bottom:1px solid var(--line);font-size:12px;border-radius:0}
.grid-refs li+li{margin-top:0}

.details{margin-top:40px;padding-top:24px;border-top:1px solid var(--line)}
.details h2+.facts{margin-top:8px}
.facts{margin:8px 0 26px;padding-left:1.2em;color:var(--ink-2);max-width:62em}
.facts li+li{margin-top:6px}

@media (max-width:900px){
  .summary-grid{grid-template-columns:1fr}
  .mm-fig{max-width:480px}
  .tiles{grid-template-columns:repeat(auto-fit,minmax(9.5rem,1fr))}
}
@media (max-width:720px){
  h1{font-size:25px}
  .rows{grid-template-columns:1fr}
  .act{padding-bottom:2px}
  .refs{margin:0 0 6px 34px;border-radius:8px}
  .row:first-child .refs,.row:last-child .refs{border-radius:8px}
  .rows .refs.is-empty{display:none}
  .card>.chips,.branches{margin-left:0}
  .branches{grid-template-columns:1fr}
}
"""

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500'
    '&family=IBM+Plex+Sans:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;700&display=swap">'
)


def build_html(fragment: bool) -> str:
    head = f"<title>{h(TITLE)}</title>\n{FONTS}\n<style>{CSS}</style>"
    body = (
        '<div class="page" lang="zh-Hant">'
        f"{header_html()}{summary_html()}{flow_html()}{details_html()}"
        "</div>"
    )
    if fragment:
        return f"{head}\n{body}\n"
    return (
        '<!doctype html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{head}\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def say(msg: str) -> None:
    """印出來。繁中 Windows 的主控台是 cp950，編不出的字換成 ?，不要讓它當掉。"""
    enc = sys.stdout.encoding or "utf-8"
    print(msg.encode(enc, "replace").decode(enc, "replace"))


def main() -> int:
    parser = argparse.ArgumentParser(description="產生專題主要架構圖（SVG）與網頁版（HTML）")
    parser.add_argument("--fragment", type=Path, help="另外輸出不含 <html>/<body> 外殼的網頁片段")
    args = parser.parse_args()

    SVG_OUT.parent.mkdir(parents=True, exist_ok=True)
    SVG_OUT.write_text(build_svg(), encoding="utf-8")
    HTML_OUT.write_text(build_html(fragment=False), encoding="utf-8")
    say(f"寫出 {SVG_OUT.relative_to(ROOT)}")
    say(f"寫出 {HTML_OUT.relative_to(ROOT)}")
    if args.fragment:
        args.fragment.write_text(build_html(fragment=True), encoding="utf-8")
        say(f"寫出 {args.fragment}")
    for w in warnings:
        say(f"警告：{w}")
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
