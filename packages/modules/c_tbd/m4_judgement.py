"""M4 判讀與抽取：判斷「是不是我這一類」「走到哪一步了」，並把敘述變成一張表（S13）。

三層退路（說明書 S13 第 4 點）：
  第一層 格式約束：讓地端小模型只能照規定格式吐
  第二層 重試兩次
  第三層 改用規則硬抽，並標記「信心低」

S3 還沒鎖定模型，所以現在每次都會走到第三層 —— 而那正是退路要能被證明會啟動的意思。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from contracts import CaseProfile, Confidence
from shared import deid, models


@dataclass
class Judgement:
    """M4 的產出。"""

    is_mine: bool
    score: float
    stage_id: str
    profile: CaseProfile
    confidence: Confidence
    notes: list[str]


_PAYMENT_TERMS = {
    "ATM": ["ATM", "提款機", "無卡存款"],
    "網路銀行轉帳": ["網銀", "網路銀行", "轉帳", "匯款"],
    "超商代碼": ["超商", "代碼繳費", "ibon", "代碼"],
    "面交現金": ["面交", "當面交", "現金"],
    "虛擬貨幣": ["USDT", "虛擬貨幣", "泰達幣", "錢包"],
}

_DAYS = re.compile(r"(\d{1,3})\s*(?:天|日)")

# S13 把 prompt、12 則示範題與 grammar 約束寫好之後改成 True。
# 在那之前不呼叫模型 —— 送一個空 prompt 過去再把回覆丟掉，只會浪費時間
# 並讓 confidence 說謊。
_SLM_PROMPT_READY = False


def keyword_score(text: str, positive: list[str], negative: list[str]) -> float:
    """最笨的分類法，當及格線（baseline）。S13 要求比它相對進步 10% 以上。

    命中數用飽和函式 hits/(hits+2) 換成分數，刻意不除以關鍵詞總數 ——
    否則關鍵詞列得越完整分數反而越低，那會逼人為了分數少列關鍵詞。

    1 命中 → 0.33　2 命中 → 0.50　3 命中 → 0.60　5 命中 → 0.71
    """
    if not text or not positive:
        return 0.0
    hits = sum(1 for term in positive if term and term in text)
    penalty = sum(1 for term in negative if term and term in text)
    effective = max(0.0, hits - 0.5 * penalty)
    if effective == 0:
        return 0.0
    return round(effective / (effective + 2), 4)


def detect_stage(text: str, stages: list[dict]) -> str:
    """從敘述判斷走到哪一步。

    規則不是「命中最多的那一階段」，而是「有命中的階段裡走得最遠的那一個」——
    playbook.yaml 的 stages 是照歷程順序寫的，所以取最後面那個。

    受害者的敘述通常會把整段經過講完（「收到簡訊…後來匯了錢」），
    命中最多的往往是最前面那一階段，那會低估他目前的處境。
    判錯要往高風險的方向錯，不能往低風險的方向錯。
    """
    chosen = ""
    for stage in stages:
        if any(cue in text for cue in stage.get("cues", [])):
            chosen = stage.get("id", "")
    return chosen


def extract_profile(text: str, *, platform_terms: list[str], stage_id: str) -> CaseProfile:
    """規則硬抽（第三層退路）。信心一律標低，因為這不是模型讀出來的。"""
    safe = deid.mask(text)

    platform = next((p for p in platform_terms if p and p in text), None)

    payment = None
    for name, terms in _PAYMENT_TERMS.items():
        if any(t in text for t in terms):
            payment = name
            break

    amount_range = next((r.placeholder for r in safe.redactions if r.kind == "amount"), None)

    days_match = _DAYS.search(text)
    days = int(days_match.group(1)) if days_match else None

    return CaseProfile(
        contact_platform=platform,
        payment_method=payment,
        amount_range=amount_range,
        scam_stage=stage_id or None,
        days_elapsed=days,
        field_confidence={
            "contact_platform": 0.4 if platform else 0.0,
            "payment_method": 0.4 if payment else 0.0,
            "amount_range": 0.6 if amount_range else 0.0,
            "scam_stage": 0.4 if stage_id else 0.0,
        },
    )


def judge(
    text: str,
    *,
    positive: list[str],
    negative: list[str],
    platform_terms: list[str],
    stages: list[dict],
    route_min: float,
) -> Judgement:
    """整個 M4 的入口。先試模型，失敗就落到規則。"""
    notes: list[str] = []
    confidence = Confidence.MEDIUM

    # 第一層 + 第二層：讓地端小模型照格式吐。S13 的 prompt 還沒寫，所以現在
    # 每次都走第三層 —— 而那正是退路要能被證明會啟動的意思。
    #
    # 🔴 這裡原本長這樣（範本帶來的，模組 A 與 B/D/E 現在也還是）：
    #
    #       models.call_slm("", grammar=None)   # 送空字串、回傳值丟掉
    #       break
    #
    #   call_slm() 還會 raise 的年代這沒差。它接上 Ollama 之後就有兩個問題：
    #   每次判讀白送一個空 prompt 給模型（暖機後 0.6 秒、冷啟動 8 秒），
    #   而且 confidence 會停在 MEDIUM —— 但 profile 仍然是下面規則硬抽的，
    #   等於對使用者謊報信心。模組 A 的測試就是這樣紅的。
    #
    #   所以在 prompt 真的寫出來之前，這裡不呼叫模型，並誠實標記信心低。
    #   S13 補 prompt 時把 _SLM_PROMPT_READY 打開，兩層重試就會接上。
    if _SLM_PROMPT_READY:
        for attempt in range(2):
            try:
                # TODO(S13)：把 prompt、few-shot 12 則、grammar 約束寫在這裡
                raise NotImplementedError("S13 的 prompt 還沒寫")
            except (models.ModelNotSelectedError, models.ModelDependencyError) as exc:
                if attempt == 1:
                    notes.append(f"退到規則抽取：{exc}")
                    confidence = Confidence.LOW
            except Exception as exc:  # 模型吐出來的東西不合格式
                if attempt == 1:
                    notes.append(f"模型輸出不合格式，退到規則抽取：{exc}")
                    confidence = Confidence.LOW
    else:
        notes.append("S13 的 prompt 尚未寫，直接用規則抽取")
        confidence = Confidence.LOW

    score = keyword_score(text, positive, negative)
    stage_id = detect_stage(text, stages)
    profile = extract_profile(text, platform_terms=platform_terms, stage_id=stage_id)

    return Judgement(
        is_mine=score >= route_min,
        score=score,
        stage_id=stage_id,
        profile=profile,
        confidence=confidence,
        notes=notes,
    )
