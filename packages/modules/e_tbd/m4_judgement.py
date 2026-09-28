"""M4 判讀與抽取：判斷「是不是我這一類」「走到哪一步了」，並把敘述變成一張表（S13）。

三層退路（說明書 S13 第 4 點）：
  第一層 格式約束：讓地端小模型只能照規定格式吐
  第二層 重試兩次
  第三層 改用規則硬抽，並標記「信心低」

S3 已經鎖定 qwen2.5:3b，第一層走 `slm_stage`（模型只抽三個事實，階段由判準導出，
理由見那支的 docstring）。模型連不上或吐不合格式時，這裡重試兩次後落到規則層。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from contracts import CaseProfile, Confidence
from shared import deid, models

from . import slm_stage, threads_stages


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


# 這個模組負責的手法，對應 threads_stages.PROFILES 的鍵
THREADS_METHOD = "網路購物"


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
    """從敘述判斷損失走到哪一步。

    主判定走 threads_stages.assess()——那是依 12,743 筆 Threads 案例全量統計
    做出來的 12 階段流程，會分辨「第幾次付款」（大額損失幾乎都發生在第二次
    之後），也會辨識決定性訊號（實名認證、索取驗證碼、遠端操作）。

    回傳的是 Harm 的名稱小寫，對應 playbook.yaml 的 stage id。

    cues 比對留作退路：assess() 只認得已做過流程分析的手法，
    遇到沒分析過的手法時退回 playbook 的關鍵字。退路的規則是
    「有命中的階段裡走得最遠的那一個」——受害者通常會把整段經過講完，
    命中最多的往往是最前面那一階段，那會低估他目前的處境。
    **判錯要往高風險的方向錯，不能往低風險的方向錯。**
    """
    try:
        assessment = threads_stages.assess(text, THREADS_METHOD)
        if assessment.stages:  # 有命中任何流程階段才採信
            return assessment.harm.name.lower()
    except Exception:  # noqa: BLE001 —— 判讀壞掉要降級，不能讓整條流程掛掉
        pass

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


# 規則層自己判得比模型好的兩個階段。
#
# 這兩階要的是**決定性訊號**（盜刷、盜轉、驗證碼、實名認證、遠端操作），
# 規則的詞表是逐筆掃 12,743 筆 Threads 案例建出來的，drained 的 recall 是 1.00。
# 模型在這兩階會把「對方要求」也算成「已經發生」——gold 60 筆上它判了 14 次
# drained 只中 5 次（precision 0.26，規則是 0.42）。
#
# 模型贏的是**數次數**：「匯了第一筆之後又匯兩次」這種要讀懂才數得出來，
# 規則靠付款動詞數，沒有動詞就整批落到「尚未付款」。gold 裡它數對 26/35。
#
# 所以分工是：決定性訊號歸規則，數次數歸模型。不是誰比較準，是兩者強項不同。
RULE_OWNED = ("drained", "credentials")


def combine_stage(rule_stage: str, call: slm_stage.StageCall | None) -> str:
    """把規則層與模型的判讀合成最後的階段。模型沒答案就全用規則的。"""
    if call is None:
        return rule_stage
    if rule_stage in RULE_OWNED:
        return rule_stage
    return slm_stage.derive(stolen=False, handed=False, paid_times=call.paid_times)


def judge(
    text: str,
    *,
    positive: list[str],
    negative: list[str],
    platform_terms: list[str],
    stages: list[dict],
    route_min: float,
) -> Judgement:
    """整個 M4 的入口。說明書 S13 第 4 點的三層退路都在這裡。

    第一層  grammar="json" 把輸出約束成合法 JSON，欄位值再由 slm_stage.parse 驗
    第二層  不合格就重試，最多兩次
    第三層  還是不行就全部退回規則，並把 confidence 標成 LOW

    模型未鎖定（ModelNotSelectedError）不重試 —— 那是設定問題，再叫一次結果一樣。
    """
    notes: list[str] = []
    confidence = Confidence.MEDIUM
    call: slm_stage.StageCall | None = None

    score = keyword_score(text, positive, negative)
    rule_stage = detect_stage(text, stages)

    # 規則已經抓到決定性訊號時不呼叫模型 —— combine_stage 本來就會丟掉它的答案，
    # 叫了也是白叫。實測一次呼叫 640ms 有 97% 花在生成，而 gold 那 60 筆裡
    # 有 40% 落在這條捷徑上。
    skip = rule_stage in RULE_OWNED
    if skip:
        notes.append(f"規則層已判定「{rule_stage}」，這一階不看模型")

    for attempt in range(0 if skip else 2):
        try:
            call = slm_stage.classify(text)
        except models.ModelNotSelectedError as exc:
            notes.append(f"退到規則抽取：{exc}")
            confidence = Confidence.LOW
            break
        except Exception as exc:  # noqa: BLE001 —— 連不上、逾時都算這次失敗
            if attempt == 1:
                notes.append(f"模型呼叫失敗，退到規則抽取：{exc}")
                confidence = Confidence.LOW
            continue
        if call:
            break
        if attempt == 1:
            notes.append("模型輸出不合格式，退到規則抽取")
            confidence = Confidence.LOW

    stage_id = combine_stage(rule_stage, call)
    profile = extract_profile(text, platform_terms=platform_terms, stage_id=stage_id)

    if call:
        # 0.6 不是「六成正確」，是「比純規則的 0.4 高一級」——
        # 實測值請看 eval/stage_slm_v1.json，那才是準確率。
        profile.field_confidence["scam_stage"] = 0.6
        if call.reason:
            notes.append(f"模型讀到：{call.reason}")
        if stage_id != rule_stage:
            notes.append(
                f"規則判「{rule_stage or '未判定'}」，依模型數到的付款次數改判「{stage_id}」"
            )

    return Judgement(
        is_mine=score >= route_min,
        score=score,
        stage_id=stage_id,
        profile=profile,
        confidence=confidence,
        notes=notes,
    )
