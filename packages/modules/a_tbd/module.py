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
# TODO(S18)：路由調校時用 20 題考題重量這兩個值。
PLATFORM_MISS = 0.6

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
        hit = _platform_hit(self.pack.platform_terms, text)
        return round(min(1.0, score * (PLATFORM_HIT if hit else PLATFORM_MISS)), 4)

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
