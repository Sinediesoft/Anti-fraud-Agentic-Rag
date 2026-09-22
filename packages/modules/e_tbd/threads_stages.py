"""Threads 詐騙判定與受害程度評估（多手法）。

依據 12,743 筆 Threads 案例全量統計。題目範圍是**起點在 Threads 的詐騙**
（99.5% 的案例 Threads 出現在其他平台之前），不限手法。

刻意只用標準函式庫 —— 沒有 import 任何模型套件、連網套件或其他模組，
之後原封不動搬進 packages/modules/x/ 也不會被 check_boundaries 擋下。

**兩個獨立維度，不要壓成一個分數：**

  1. 這是不是詐騙  → Verdict + Confidence
  2. 損失到什麼地步 → Harm

壓成單一「風險分數」會出現「已經把錢匯給陌生人，卻顯示低風險」的矛盾。

**沒有「安全」這個結論。** 語料 194,355 筆全部是詐騙報案，一筆正常交易都沒有，
系統沒有資格說任何情況安全。訊號不足時一律回「資訊不足」並要求補充細節。

**手法各有各的流程。** 購物詐騙是「假物流 → 實名認證 → 假客服」，
投資詐騙是「群組 → 帳面獲利 → 加碼 → 無法出金」，兩者的決定性訊號完全不同，
硬套同一套階段會判錯。所以每個手法一個 Profile。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Verdict(StrEnum):
    SCAM = "是詐騙"
    LIKELY = "疑似詐騙"
    UNKNOWN = "資訊不足，無法判斷"


class Confidence(StrEnum):
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class Harm(StrEnum):
    NONE = "尚未發生損失"
    PAID = "已付款"
    REPEATED = "已重複付款"
    CREDENTIALS = "已交付帳戶控制權"
    # 2026-09-22 改名：原本叫「資金已遭轉出」，60 筆人工標註中有 24 筆把
    # 「我匯錢被騙走」也歸到這級——名字在對抗定義，所以說明輸了。
    # 這級指的是**帳戶被別人動用**（盜刷、盜轉），不是自己匯出去的損失。
    DRAINED = "帳戶遭盜用"


@dataclass(frozen=True)
class Stage:
    no: int
    name: str
    pattern: str
    hit_rate: float  # 該手法語料中的命中率
    _rx: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_rx", re.compile(self.pattern))

    def search(self, text: str):
        return self._rx.search(text)


@dataclass(frozen=True)
class Profile:
    """一個手法的完整判定設定。"""

    method: str
    stages: tuple[Stage, ...]
    decisive: dict[int, str]  # 階段編號 → 為什麼合法流程不會有
    suspicious: dict[int, str]
    harm_labels: dict[Harm, str]  # 該手法下 Harm 的說法
    actions: dict[Harm, list[str]]
    pay_stage: int  # 哪個階段代表「付錢」，用來數重複付款


# ══════════════════════════════════════════════════════════════════
# 購物詐騙（9,178 筆，佔 Threads 案例 72.0%）
# ══════════════════════════════════════════════════════════════════

SHOPPING = Profile(
    method="網路購物",
    stages=(
        Stage(1, "看到貼文", r"看到|刷到|滑到|瀏覽|廣告|貼文|限時動態", 0.686),
        Stage(2, "私訊接觸", r"私訊|傳訊|留言|詢問|聯繫|加.{0,2}好友", 0.773),
        Stage(3, "談價下單", r"下單|訂購|購買|談好|議價|報價|訂金|下訂", 0.545),
        Stage(4, "假物流連結", r"賣貨便|交貨便|店到店|7-?11|全家|萊爾富|物流|寄件|超商", 0.475),
        # \b 在中文語境失效（中文字也算 \w），改用消耗前後字元的寫法。
        # Rust regex 不支援 lookaround，所以不能用 (?<!)。
        Stage(
            5,
            "轉移平台",
            r"(?i)(^|[^a-z])line([^a-z]|$)|加賴|萊恩|加.{0,2}賴|telegram|facetime",
            0.572,
        ),
        Stage(6, "實名認證話術", r"實名制|實名認證|未完成認證|認證失敗|身分驗證|需要驗證", 0.395),
        Stage(7, "假客服介入", r"客服|專員|線上客服|銀行人員|行員|業務員", 0.560),
        Stage(8, "匯款付款", r"匯款|匯了|轉帳|付款|匯入|支付|轉了|匯過去", 0.742),
        # S10 修正（2026-09-22）：原本只要出現「網銀／網路銀行／ATM」就算遠端操作，
        # 但那是台灣人描述付款方式最常見的講法（「我用網銀轉帳」）。
        # 60 筆人工標註中，這條誤判了 4 筆，全部是「透過網路銀行轉了錢」= 付款不是被操控。
        # 改成要有**對方指揮我操作**的語境，或本來就只會出現在詐騙裡的行為。
        Stage(
            9,
            "遠端操作",
            # 第二版（2026-09-22）：第一版加了「照指示操作」這類語境詞，結果命中
            # 「我就依照指示操作」這種 165 案例的敘事套語，credentials 從 12 筆暴增到 27。
            # 收緊成「只有詐騙流程才會出現的具體行為」——
            # 一般網購不會共享畫面、不會到 ATM 做設定、不會為了收款去做網銀驗證。
            r"(共享|分享)畫面|畫面(共享|分享)|遠端(桌面|控制|連線|軟體)"
            r"|AnyDesk|TeamViewer|向日葵"
            r"|(ATM|提款機|自動櫃員機)[^。]{0,14}(設定|無卡|解除|英文|轉換|按鍵)"
            r"|(網銀|網路銀行|網路ATM)[^。]{0,10}(驗證|認證|解除|綁定|設定)",
            0.292,
        ),
        Stage(10, "索取驗證碼", r"驗證碼|OTP|一次性密碼|簡訊碼|動態密碼", 0.030),
        Stage(
            11,
            "資金流出",
            # S10 修正（2026-09-22）：原本單獨的「轉出」「扣款」就算數，但
            # 「我把錢轉出去」是付款、「只要掃碼扣款就能完成交易」是對方的話術。
            # 60 筆裡這條誤判 4 筆（全是「扣款」觸發）。改成要有被動語態或明確的非自願。
            r"(存款|帳戶|款項|餘額|存簿|戶頭).{0,8}(被|遭)[^。，]{0,6}(轉出|盜領|盜刷|扣|提領|領走|轉走)"
            r"|(被|遭).{0,3}(盜刷|盜領|盜轉)"
            r"|不明.{0,4}(交易|扣款|消費|支出|款項)",
            0.188,
        ),
    ),
    decisive={
        6: "正規超商物流與賣貨便沒有「實名認證」這道手續，這句話本身就是詐騙話術",
        9: "銀行與物流業者不會請你操作 ATM、網銀或安裝 APP",
        10: "驗證碼等同密碼，任何人以任何理由索取都不合理",
        11: "帳戶已發生非自願的資金異動",
    },
    suspicious={
        4: "對方提供的物流／賣貨便連結常是仿冒的釣魚網站",
        5: "把交易帶離平台，就沒有任何紀錄可以申訴",
        7: "一般交易糾紛不會由賣家「轉接客服」處理",
    },
    harm_labels={
        Harm.NONE: "尚未付款",
        Harm.PAID: "已付款一次",
        Harm.REPEATED: "已重複付款",
        Harm.CREDENTIALS: "已交付帳戶控制權",
        Harm.DRAINED: "帳戶遭盜用",
    },
    actions={
        Harm.NONE: [
            "**先不要付錢，也不要離開平台交易。** 轉到 LINE 之後就沒有紀錄可以申訴。",
            "查對方帳號的建立時間、貼文歷史與追蹤者。新開帳號、貼文稀少要特別小心。",
            "價格明顯低於市價是最強的警訊。",
            "堅持使用平台內建或有第三方保障的付款方式，不要匯到個人帳戶。",
        ],
        Harm.PAID: [
            "**不要再付第二筆。** 接下來若出現「認證失敗」「訂單卡住」「要補手續費」，那都是話術。",
            "保留全部對話紀錄、匯款單據與對方帳號截圖。",
            "打 **165** 說明狀況，並聯繫你的銀行。",
            "若是超商賣貨便交易，直接打超商官方客服查證訂單是否真的存在。",
        ],
        Harm.REPEATED: [
            "**立刻停止，不要再匯任何一筆。** 對方會持續用新理由要求付款，直到你停手為止。",
            "打 **165**，並聯繫銀行說明遭詐，詢問能否對收款帳戶通報。",
            "整理兩次匯款的時間、金額、收款帳號，報案時會需要。",
            "不要接受「全額退款但要先付保證金」這種說法，那是第三次。",
        ],
        Harm.CREDENTIALS: [
            "**立刻打你銀行卡背面或官網上的客服電話**（不要用對方給的號碼）辦理停卡與圈存。",
            "變更網銀密碼與相關帳號密碼。",
            "若曾開啟遠端或畫面分享，移除該軟體並檢查裝置。",
            "打 **165**，說明帳戶可能已被操作。",
            "若提款卡或存摺已寄出，一併向銀行與警方說明 —— 帳戶可能被用作人頭戶。",
        ],
        Harm.DRAINED: [
            "**現在就打銀行客服辦理圈存與停卡**，號碼用卡片背面或官網的。",
            "打 **165**，說明資金已遭轉出。",
            "帶對話紀錄、匯款單據到警察局報案，取得受理案件證明。",
            "變更所有相關密碼；曾開啟遠端的裝置要檢查。",
            "向銀行詢問能否對收款帳戶申請圈存 —— 越早通報，攔截機會越高。",
        ],
    },
    pay_stage=8,
)


# ══════════════════════════════════════════════════════════════════
# 投資詐騙（1,363 筆，佔 10.7%）
#
# 流程與購物類完全不同。統計顯示「老師帶單」只有 11.2%，不是主流；
# 真正的主軸是「加入群組」（68.0%）與「帳面獲利」（75.9%）。
# 決定性訊號是**無法出金**與**出金前要求額外費用** —— 合法券商不存在這兩件事。
# ══════════════════════════════════════════════════════════════════

INVESTMENT = Profile(
    method="投資詐騙",
    stages=(
        Stage(21, "看到廣告貼文", r"看到|刷到|滑到|廣告|貼文|限時動態|直播", 0.615),
        Stage(22, "加入群組", r"群組|社團|加入|拉我進|邀請", 0.680),
        Stage(23, "帳面獲利", r"獲利|賺|盈利|報酬|漲|收益|翻倍", 0.759),
        Stage(24, "老師分析師", r"老師|分析師|助理|導師|講師|專家|帶單|喊單", 0.112),
        Stage(25, "下載投資APP", r"下載|安裝|APP|應用程式|註冊.{0,4}(平台|帳號|會員)", 0.252),
        Stage(26, "虛擬貨幣", r"虛擬貨幣|加密|USDT|比特幣|以太|泰達", 0.465),
        Stage(27, "入金", r"入金|儲值|投入|存入|匯入.{0,6}(平台|帳戶)", 0.292),
        Stage(
            28,
            "出金成功（誘餌）",
            r"(成功|順利).{0,4}出金|出金.{0,4}(成功|順利)|領.{0,2}出來",
            0.076,
        ),
        Stage(29, "加碼大額", r"加碼|追加|再投|加大|更多資金|大筆", 0.187),
        Stage(30, "被要求額外費用", r"稅金|手續費|保證金|解凍|驗資|保金|違約金|解鎖", 0.123),
        Stage(
            31,
            "無法出金",
            r"(無法|不能|沒辦法|不給|不讓).{0,4}(出金|提領|領出)|(出金|提領).{0,4}(失敗|不了|卡住|被拒)|卡住|凍結|鎖定",
            0.240,
        ),
    ),
    decisive={
        31: "合法券商與交易所不會讓你「無法出金」，出金受阻本身就是詐騙的核心特徵",
        30: "出金前要求先繳稅金、保證金或解凍費 —— 真實的稅是從所得扣繳，不是先付錢才能領",
    },
    suspicious={
        22: "投資標的透過社群群組招攬，不是合法的證券業務招攬方式",
        28: "小額出金成功往往是誘餌，目的是讓你敢投入更大筆",
        29: "在看到帳面獲利後被鼓勵加碼，是投資詐騙的標準節奏",
    },
    harm_labels={
        Harm.NONE: "尚未入金",
        Harm.PAID: "已入金",
        Harm.REPEATED: "已加碼投入更多",
        Harm.CREDENTIALS: "已交付帳戶或身分資料",
        Harm.DRAINED: "資金無法取回",
    },
    actions={
        Harm.NONE: [
            "**不要入金。** 合法的證券、期貨業務不會透過社群群組招攬。",
            "到金管會「證券期貨局」網站查這家平台有沒有合法登記 —— 查不到就是地下平台。",
            "任何「保證獲利」「穩賺不賠」的說法在合法投資裡都不存在。",
            "退出群組，封鎖對方。群組裡的「賺錢見證」多半是同一組人演的。",
        ],
        Harm.PAID: [
            "**不要再投入任何資金。** 現在看到的「帳面獲利」是對方後台顯示的數字，不是真的。",
            "立刻嘗試出金 —— 出金受阻就確定是詐騙，不要相信任何補件、繳費的說法。",
            "截圖保存平台頁面、群組對話、入金紀錄。",
            "打 **165**，並聯繫銀行說明匯款對象可能是詐騙帳戶。",
        ],
        Harm.REPEATED: [
            "**停止，不要再加碼。** 帳面數字漲得越漂亮，越是要你投更多的誘餌。",
            "現在就試著全額出金。受阻的話立刻停損，不要再投錢「解鎖」。",
            "整理每一筆入金的時間、金額、收款帳號。",
            "打 **165** 並報案。越早通報，收款帳戶被圈存的機會越高。",
        ],
        Harm.CREDENTIALS: [
            "**立刻打銀行客服**（用卡片背面或官網號碼）辦理停卡與圈存。",
            "變更網銀與所有相關帳號密碼。",
            "若曾上傳身分證、存摺照片，向銀行說明可能遭冒用開戶。",
            "打 **165**，說明個資與帳戶資料已外流。",
        ],
        Harm.DRAINED: [
            "**不要付「解凍費」「稅金」「保證金」。** 那是第二輪詐騙，付了錢也領不回來。",
            "特別小心自稱能幫你「追回款項」的人 —— 那是針對受害者的二次詐騙。",
            "打 **165**，帶所有入金紀錄與對話到警局報案。",
            "向銀行申請匯款帳戶圈存，並保留受理案件證明。",
        ],
    },
    pay_stage=27,
)


# 其他手法（色情應召 804、求職打工 311、貸款信用 297…）流程尚未分析。
# 先用購物類的通用部分處理，並在介面明確標示「此類型的流程分析尚未完成」。
PROFILES: dict[str, Profile] = {
    "網路購物": SHOPPING,
    "投資詐騙": INVESTMENT,
}
FALLBACK = SHOPPING
ANALYSED = frozenset(PROFILES)


@dataclass(frozen=True)
class Assessment:
    method: str
    method_analysed: bool  # 這個手法是否已做過流程分析
    verdict: Verdict
    confidence: Confidence
    decisive: list[tuple[str, str]]
    suspicious: list[tuple[str, str]]
    harm: Harm
    harm_label: str
    harm_detail: str
    stages: list[Stage]


def _count(profile: Profile, text: str) -> int:
    stage = next(s for s in profile.stages if s.no == profile.pay_stage)
    return len(re.findall(stage.pattern, text))


def _harm_of(profile: Profile, nos: set[int], pays: int) -> tuple[Harm, str]:
    if profile is INVESTMENT:
        if 31 in nos or 30 in nos:
            return Harm.DRAINED, (
                "已出現出金受阻或被要求額外費用。這個階段的資金通常已經拿不回來，"
                "而且接下來很可能出現第二輪詐騙。"
            )
        if 29 in nos or pays >= 2:
            return Harm.REPEATED, "已加碼投入。語料顯示大額損失幾乎都發生在看到帳面獲利之後。"
        if 27 in nos:
            return Harm.PAID, "已入金。現在最重要的是立刻嘗試全額出金，測試平台是不是真的。"
        return Harm.NONE, "描述中還沒有提到實際入金。"

    if 11 in nos:
        return Harm.DRAINED, "描述中已提到資金遭轉出或盜刷，這是最緊急的狀態。"
    if nos & {9, 10}:
        return Harm.CREDENTIALS, (
            "已進行遠端操作或提供驗證碼，等於把帳戶控制權交出去 —— "
            "即使目前還沒看到損失，帳戶仍在對方可操作的狀態。"
        )
    if pays >= 2:
        return Harm.REPEATED, "已付款兩次以上。追加要求付款是損失擴大的關鍵轉折。"
    if 8 in nos:
        return Harm.PAID, "已付款一次。重點是不要再付第二筆。"
    return Harm.NONE, "描述中還沒有提到金錢或資料交付。"


_REPEAT_REASON = "被要求再付一次 —— 語料顯示大額損失幾乎都發生在第二次付款之後"


# 跨手法的決定性訊號：不管哪一類詐騙，命中就是詐騙。
# 未分析的手法（色情應召、求職打工…）套通用 profile 時，至少這些接得住。
#
# 「寄提款卡」特別重要 —— 後果比被騙錢嚴重：帳戶被當人頭戶使用，
# 受害者可能被列為警示戶，甚至因幫助詐欺被偵辦。測試「家庭代工要我寄提款卡」
# 時發現通用規則只回「不要付錢」，完全沒接住這個風險，因此獨立出來。
UNIVERSAL: tuple[tuple[str, str, str, str], ...] = (
    (
        # 名稱與購物 profile 的 Stage 10 一致，否則同一件事會被列兩次
        "索取驗證碼",
        r"驗證碼|OTP|一次性密碼|簡訊碼|動態密碼",
        "驗證碼等同密碼，任何人以任何理由索取都不合理",
        "**不要把驗證碼給任何人。** 已經給出去的話，立刻打銀行客服"
        "（用卡片背面或官網號碼）辦理停卡，並變更網銀密碼。",
    ),
    (
        "寄出金融卡或存摺",
        r"(寄|交|給).{0,8}(提款卡|金融卡|存摺|信用卡|帳戶資料)"
        r"|(提款卡|金融卡|存摺).{0,6}(寄|交|給)",
        "任何合法工作、貸款或交易都不需要你的提款卡或存摺。帳戶會被拿去收受詐騙款項，"
        "你可能被列為警示戶，甚至因幫助詐欺被偵辦 —— 這比損失金錢嚴重",
        "**不要寄出提款卡、存摺或信用卡。** 已經寄出的話，立刻打銀行客服辦理掛失止付，"
        "然後**主動到警察局說明** —— 主動報案能證明你是被害人而不是共犯，不要因為害怕而拖延。",
    ),
    (
        "提供證件影本",
        r"(身分證|健保卡|護照|駕照).{0,6}(照片|影本|正反面|拍照|傳給)",
        "證件影本足以讓對方冒名申辦帳戶或門號",
        "**不要傳證件照片。** 已經傳出去的話，向銀行說明可能遭冒名申辦，"
        "並考慮到聯徵中心辦理當事人通報。",
    ),
)
_UNIVERSAL_RX = tuple((n, re.compile(pat), why, act) for n, pat, why, act in UNIVERSAL)

UNIVERSAL_TAIL = [
    "打 **165** 說明狀況。",
    "檢查帳戶是否已有不明進出，並向銀行申請交易明細。",
]


def assess(text: str, method: str) -> Assessment:
    """依手法判定是否為詐騙（含信心）與受害程度。

    判定規則：
      決定性訊號 ≥1            → 是詐騙（2 個以上信心高，1 個信心中）
      可疑訊號 ≥2              → 疑似詐騙，信心中
      其餘                     → 資訊不足，信心低

    「資訊不足」永遠不等於安全，只代表描述裡的資訊不夠判斷。
    """
    profile = PROFILES.get(method, FALLBACK)
    stages = [s for s in profile.stages if s.search(text)]
    nos = {s.no for s in stages}
    by_no = {s.no: s.name for s in stages}
    pays = _count(profile, text)

    decisive = [(by_no[n], why) for n, why in profile.decisive.items() if n in nos]
    universal_hit = [(name, why) for name, rx, why, _ in _UNIVERSAL_RX if rx.search(text)]
    for name, why in universal_hit:
        if name not in {d[0] for d in decisive}:
            decisive.append((name, why))
    suspicious = [(by_no[n], why) for n, why in profile.suspicious.items() if n in nos]
    if pays >= 2:
        suspicious.append(("被追加要求付款", _REPEAT_REASON))

    if decisive:
        verdict = Verdict.SCAM
        confidence = Confidence.HIGH if len(decisive) >= 2 else Confidence.MEDIUM
    elif len(suspicious) >= 2:
        verdict, confidence = Verdict.LIKELY, Confidence.MEDIUM
    else:
        verdict, confidence = Verdict.UNKNOWN, Confidence.LOW

    harm, detail = _harm_of(profile, nos, pays)
    if universal_hit and harm in (Harm.NONE, Harm.PAID, Harm.REPEATED):
        harm = Harm.CREDENTIALS
        detail = (
            "已交出（或被要求交出）帳戶、卡片或證件。就算目前還沒看到金錢損失，"
            "帳戶已在對方可使用的狀態，而且可能被拿去收受他人的詐騙款項。"
        )
    return Assessment(
        method=method,
        method_analysed=method in ANALYSED,
        verdict=verdict,
        confidence=confidence,
        decisive=decisive,
        suspicious=suspicious,
        harm=harm,
        harm_label=profile.harm_labels[harm],
        harm_detail=detail,
        stages=stages,
    )


def actions_for(method: str, harm: Harm, stages: list[Stage], text: str = "") -> list[str]:
    """依手法與受害程度挑行動清單，並依實際命中的階段微調第一句。"""
    profile = PROFILES.get(method, FALLBACK)
    actions = list(profile.actions[harm])
    hit_actions = [act for _, rx, _, act in _UNIVERSAL_RX if rx.search(text)]
    if hit_actions:
        # 命中通用訊號時，那些建議排在最前面 —— 帳戶風險比金錢損失優先
        actions = hit_actions + UNIVERSAL_TAIL + [a for a in actions if "165" not in a]

    # 購物類 C 區有兩種進來的方式，講錯第一句會答非所問
    if profile is SHOPPING and harm is Harm.REPEATED and not {6, 7} & {s.no for s in stages}:
        actions[0] = (
            "**對方要你「再匯一次」，風險很高。** 不管理由是訂單卡住、認證失敗、"
            "金額填錯還是要付手續費 —— 語料顯示大額損失幾乎都發生在第二次付款之後。"
        )
    return actions
