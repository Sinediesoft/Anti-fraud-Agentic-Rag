"""模組 A 的四個進入點（說明書 S4 / S14 第 2 點）。

這是你唯一要跟外界對齊的地方。外殼只透過這四個函式認識你，
裡面的 M1–M5 怎麼寫完全自由 —— OCR 用哪套、向量庫用哪個、prompt 怎麼寫，
都是你的決定。

平台 × 手法填進 pack.yaml 之前，can_handle() 一律回 0。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import yaml
from contracts import (
    AnalyzeInput,
    HealthCheck,
    HealthReport,
    ModuleInfo,
    PackSpec,
    Verdict,
)
from shared import models

from . import m1_corpus, m2_vision, m4_judgement, m5_agent

MODULE_DIR = Path(__file__).resolve().parent

# 平台條件在路由上是乘法，而且是雙邊的：對得上放大、對不上打折。
#
# 只做單邊（對不上打折、對得上不加）在 2026-09-21 量出來是退步：加法那版
# 的 +0.15 是獎勵，拿掉之後 A 在自己的案子上反而掉分 —— 小寫 LINE 的案子
# 從 0.75 掉到 0.60，本來贏 c_tbd 的 0.7143，變成輸。折扣只打在 A 身上，
# 因為 c_tbd 沿用加法而那不是這裡能改的（CODEOWNERS）。
#
# 所以獎勵要留著，只是從「加 0.15」換成「乘 1.3」—— 乘法對高分的案子
# 獎勵更多，而那正是我們想要的：手法像、平台又對，就該拉開距離。
PLATFORM_HIT = 1.3
# 對不上時打的折。0.6 讓「2 命中 + 平台不符」（0.50）掉到 0.30、
# 「4 命中 + 平台不符」（0.667）掉到 0.40，兩個都明確落在 route_min 0.50
# 之下 —— 手法再像，平台不對就不認領。
PLATFORM_MISS = 0.6
# 沒提到任何平台時的係數。
#
# 2026-09-22 之前這裡只有兩態：有我的平台詞乘 1.3，其餘一律乘 0.6。那把
# 「沒提任何平台」跟「提到別人的平台」當成同一件事，但它們不是 ——
# 前者是資訊不足，後者是證據指向別人。
#
# 實測代價：20 題考題裡 10 題不含平台詞的（受害者真實的打字方式）在兩態下
# 全軍覆沒，10/20。連本專案的起點例句「群組裡的老師叫我先入金才能出金」
# 都只有 0.30。原因是飽和函式配 0.6 的天花板：要 10 個命中才碰得到
# route_min 0.50，而一句口語塞不進 10 個手法詞。
#
# 中性不是放寬，是不再倒扣。分數仍然要靠手法詞自己掙到 2 個命中（0.50）
# 才過門檻；提到別人平台的照樣打 0.6 的折。
PLATFORM_NEUTRAL = 1.0

# 別人的平台詞。只用來判斷「這句話指向別人的平台」，不參與計分。
#
# 為什麼寫在這裡而不是 pack.yaml：PackSpec 設了 extra="forbid"，加欄位要動
# 凍結的 packages/contracts/（全員同意才能改）。而 negative_terms 是餵給
# keyword_score 扣命中數的，語意是「話術詞」不是「平台名」，借來用會讓
# 那個欄位變成兩種東西。所以跟 PLATFORM_HIT 一樣當常數放這裡。
#
# 這是字面複製，不是 import —— 沒有違反「模組互不相認」（規矩二）。
# 代價是別人改題目時這份會過期，所以只收**不會誤判的平台專名**：
#   · 不收「社團」—— C 的 platform_terms 有，但 LINE 社群也叫社團
#   · 不收「超商」—— D 的題目是超商物流，但 A 的案子裡常出現超商繳費
#   · 不收「脆」—— PR #41 實測：明確指 Threads 的 73 筆，誤中（脆弱、
#     乾脆、酥脆）1,167 筆，16 倍雜訊
# TODO(S18)：PR #39／#41 合併後回來對一次，D 與 E 的題目那時才定案。
OTHER_PLATFORM_TERMS = (
    "Facebook",
    "facebook",
    "FB",
    "fb",
    "臉書",
    "粉專",
    "粉絲專頁",
    "Threads",  # E 在 PR #41 宣告，尚未合併
)

# 平台詞的比對：英文詞要卡字界，中文詞直接比子字串。
#
# 「line」是子字串，online / Online / ONLINE 都含有它。2026-09-21 掃全部
# 194,355 筆共用語料：有 389 筆含這類英文字，其中 138 筆完全沒提到真的
# LINE 卻會被判成「平台對得上」。改成不分大小寫比對也救不了這個 —— 那是
# 兩件事（大小寫的部分改用詞表列舉處理，見 pack.yaml 的 platform_terms）。
#
# 只對純 ASCII 的詞卡字界：中文沒有 a-z 的字界概念，「加賴」照原樣比。
_ASCII = re.compile(r"^[A-Za-z]+$")


def _platform_hit(terms: Iterable[str], text: str) -> bool:
    for term in terms:
        if not term:
            continue
        if _ASCII.match(term):
            if re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", text):
                return True
        elif term in text:
            return True
    return False


def _platform_factor(terms: Iterable[str], text: str) -> float:
    """三態：有我的平台 → 放大；只有別人的平台 → 打折；都沒提 → 中性。

    自己的平台優先，兩邊都出現時算自己的。這不是偷分，是照語料的實際樣態：
    A 的 17,764 筆是「假投資 ∩ 內文提得到 LINE」切出來的，而這一類的典型
    歷程就是在別的平台看到廣告、再被導進 LINE 談 —— 讀語料時三個情境的
    取樣裡幾乎每一筆都同時出現 Facebook 與 LINE。若「有別人的平台」就打折，
    A 會把自己語料的大半判成不是自己的。
    """
    if _platform_hit(terms, text):
        return PLATFORM_HIT
    if _platform_hit(OTHER_PLATFORM_TERMS, text):
        return PLATFORM_MISS
    return PLATFORM_NEUTRAL


class ModuleA:
    """模組 A。組合決定後記得把類別名稱也換成看得懂的名字。"""

    def __init__(self) -> None:
        self.pack = PackSpec.load(MODULE_DIR / "pack.yaml")
        self.playbook = (
            yaml.safe_load((MODULE_DIR / "playbook.yaml").read_text(encoding="utf-8")) or {}
        )

    # ── 進入點 1 ────────────────────────────────────────────
    def can_handle(self, payload: AnalyzeInput) -> float:
        """這個案子有多像我負責的類型，回 0 到 1。

        要保守：不確定就給低分，讓外殼判定「尚未涵蓋」。
        分錯科比查不到更糟。
        """
        if not self.pack.is_configured:
            # 平台 × 手法還沒決定的模組不搶案子
            return 0.0

        text = payload.text
        for image in payload.images:
            text += "\n" + m2_vision.read_screenshot(image).plain_text

        score = m4_judgement.keyword_score(
            text,
            positive=list(self.pack.route_terms) + list(self.pack.labels_canon),
            negative=list(self.pack.negative_terms),
        )
        # 「平台 × 手法」在路由上是乘法不是加法：加法讓手法詞夠多就能蓋過
        # 平台不符 —— 實測一個臉書的案子在加法下拿到 0.50，剛好等於
        # route_min，A 照樣認領了不是自己平台的案子。乘法之後掉到 0.30。
        return round(min(1.0, score * _platform_factor(self.pack.platform_terms, text)), 4)

    # ── 進入點 2 ────────────────────────────────────────────
    def analyze(self, payload: AnalyzeInput) -> Verdict:
        return m5_agent.run(payload, self.pack, self.playbook)

    # ── 進入點 3 ────────────────────────────────────────────
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            id=self.pack.id,
            code=self.pack.code,
            name=self.pack.name,
            plan=self.pack.plan,
            platform=self.pack.platform,
            tactic=self.pack.tactic,
            owner=self.pack.owner,
            version=self.pack.version,
        )

    # ── 進入點 4 ────────────────────────────────────────────
    def health(self) -> HealthReport:
        """我準備好了沒。required=False 的項目壞了只會降級，不會擋啟動。"""
        checks = [
            HealthCheck(
                name="pack",
                ok=self.pack.is_configured,
                detail="平台 × 手法與標籤都填了"
                if self.pack.is_configured
                else "平台 × 手法尚未決定",
                required=False,
            ),
            HealthCheck(
                name="playbook",
                ok=bool(self.playbook.get("stages")),
                detail=f"{len(self.playbook.get('stages', []))} 個階段",
                required=True,
            ),
            HealthCheck(
                name="corpus",
                ok=bool(m1_corpus.load_local()),
                detail="自己的語料檔還沒切出來（S9）" if not m1_corpus.load_local() else "",
                required=False,
            ),
            HealthCheck(
                name="ocr",
                ok=m2_vision.OCR_ENGINE is not None,
                detail="OCR 引擎尚未選定（S11），有圖時會降級成純文字",
                required=False,
            ),
            HealthCheck(
                name="models",
                ok=models.all_locked(),
                detail="模型尚未鎖定（S3），M4 會走規則退路",
                required=False,
            ),
        ]
        return HealthReport(
            module_id=self.pack.id,
            ready=not [c for c in checks if not c.ok and c.required],
            checks=checks,
        )


def build_module() -> ModuleA:
    """外殼靠這個工廠函式拿到實例。函式名稱不能改（contracts.ENTRYPOINT_FACTORY）。"""
    return ModuleA()
