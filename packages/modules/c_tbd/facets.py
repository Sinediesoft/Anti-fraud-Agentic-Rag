"""結構化分類：把案例正規化成可精確比對的值（M3 的一部分）。

為什麼需要這一步：純向量檢索答不好「篩選型」問題。問「Facebook 上有哪些
投資詐騙」時，語意相似度沒辦法保證「全部」—— 它只會給你最像的前 k 個。
但管道、類型這些欄位本來就是結構化的，直接篩就能保證完整。

移植自另一個本機 repo 的 shared/facets.py（見 README.md 的移植計畫）。

🔴 移植時最大的轉折：derive() 的職責整個翻回來了。

    來源那套的語料是「自己訂格式、自己寫」的 6 張 TOML 案例卡，欄位本來就
    乾淨，所以 derive() 的工作是**驗證** —— 填錯的值當場報錯，而不是靜靜
    產生一個永遠篩不到的 facet。

    本專案的語料是 165 的原始案件記錄，只有六個欄位（編號、日期、縣市、
    縣市代號、內文、標籤），**沒有管道、付款方式、對象這些欄**。所以
    derive() 只能退回去從自由文字**推斷** —— 也就是來源註解裡說「職缺版
    有一半程式碼在處理例外」的那個模式。

    這不是退步，是語料性質決定的：中介格式把成本移到寫語料的當下，而我們
    沒有寫語料的機會，只能接受推斷的不確定性。代價寫在 derive_from_text()
    的註解裡。
"""

from __future__ import annotations

import re

# ── 詞彙表：key 是存進 facets 的正規值，list 是問句裡可能出現的說法 ──────
#
# 每一張表同時給兩邊用：
#   · derive_from_text()  從案件內文推斷分類
#   · parse_query()       從問句抓條件
# 共用所以不會對不起來 —— 這是保證「建索引」與「查詢」分類一致的機制。

CATEGORY = {
    "假投資": ["假投資", "投資詐騙", "投資", "股票", "飆股", "帶單", "期貨", "虛擬貨幣", "幣圈"],
    "假交友": ["假交友", "交友詐騙", "交友", "網戀", "感情", "殺豬盤", "約會"],
    "假冒身分": [
        "假冒身分",
        "假冒",
        "冒充",
        "檢警",
        "警察",
        "地檢署",
        "公務機關",
        "健保",
        "國稅局",
    ],
    "購物詐騙": ["購物詐騙", "網購", "購物", "網拍", "賣家", "買家", "分期", "退款"],
    "釣魚訊息": ["釣魚", "詐騙簡訊", "假簡訊", "包裹", "中獎", "補助", "釣魚訊息"],
    "求職詐騙": ["求職詐騙", "求職", "找工作", "打工", "應徵", "車手", "人頭帳戶"],
}

CHANNEL = {
    "電話": ["電話", "來電", "打來", "語音"],
    "簡訊": ["簡訊", "sms"],
    "LINE": ["line", "賴"],
    "Facebook": ["facebook", "fb", "臉書", "粉專", "粉絲專頁", "社團"],
    "Instagram": ["instagram", "ig"],
    "交友軟體": ["交友軟體", "交友app", "tinder", "配對"],
    "電子郵件": ["電子郵件", "email", "e-mail", "郵件", "信箱"],
    "網頁廣告": ["網頁廣告", "廣告", "一頁式"],
}

PAYMENT = {
    "ATM轉帳": ["atm", "提款機"],
    "網銀匯款": ["網銀", "匯款", "網路銀行", "轉帳"],
    "超商代碼": ["超商", "代碼繳費"],
    "虛擬貨幣": ["虛擬貨幣", "usdt", "比特幣", "加密貨幣", "泰達幣"],
    "遊戲點數": ["遊戲點數", "點數卡", "mycard", "遊戲儲值"],
    "面交": ["面交", "當面", "車手收錢"],
    "信用卡": ["信用卡", "刷卡", "盜刷"],
}

TARGET = {
    "長者": ["長者", "老人", "長輩", "阿公", "阿嬤", "銀髮"],
    "學生": ["學生", "大學生", "高中生", "年輕人"],
    "求職者": ["求職者", "待業"],
    "投資人": ["投資人", "散戶", "投資者"],
    "網購族": ["網購族", "消費者"],
    "上班族": ["上班族", "職員"],
    "單身族": ["單身", "單身族"],
    "家庭主婦": ["家庭主婦", "主婦"],
    "賣家": ["賣家", "二手賣家"],
    "退休族": ["退休族", "退休"],
}

# 問句裡的概括說法，展開成具體的值。**只用在查詢側**。
#
# 為什麼需要：問「網路上有哪些詐騙」時 parse_query 抽不出任何條件，於是退回
# 純向量檢索，撈回來的是語意相近但管道不對的案例。展開成管道之後就沿用既有的
# channel 過濾，derive 與 match() 一行都不用改。
CHANNEL_GROUP = {
    "網路": ["LINE", "Facebook", "Instagram", "交友軟體", "電子郵件", "網頁廣告"],
    "社群": ["Facebook", "Instagram"],
    "通訊軟體": ["LINE"],
    "手機": ["電話", "簡訊", "LINE"],
}

TABLES = {"category": CATEGORY, "channel": CHANNEL, "payment": PAYMENT, "target": TARGET}
MULTI = tuple(TABLES)


def _hits(text: str, table: dict[str, list[str]]) -> list[str]:
    t = (text or "").lower()
    return [key for key, words in table.items() if any(w in t for w in words)]


class UnknownFacet(ValueError):
    """有人填了詞彙表裡沒有的值。

    刻意做成例外而不是警告：這種錯誤的症狀是「資料存在但永遠篩不到」，
    不會有任何跡象。來源那套就吃過這個虧 —— 有欄位被填錯，那幾筆的分類
    從此是空的，沒人發現。建索引時就炸掉最便宜。
    """


def derive_from_text(text: str, label: str = "") -> dict[str, list[str]]:
    """從案件內文推斷 facets。建索引時每筆呼叫一次。

    ⚠ 這是推斷不是驗證，所以有兩種錯誤，而且方向相反：

      · 漏抓（false negative）：受害者寫「他叫我加賴」而沒寫 LINE 兩個字，
        或整篇沒提管道。結果是空 list —— 代表「這筆沒記載」而不是「否」。
        誠實留空，不要猜，篩選時它就不會被撈到。

      · 錯抓（false positive）：內文提到「我沒有在臉書上看到廣告」也會被
        抓成 Facebook。純字串比對看不懂否定句。

    漏抓的代價是篩選型問題答不完整，錯抓的代價是撈回不相關的案例。目前
    兩者都沒有量過 —— 要量得等 S12 的標註評測集。在那之前這個函式的產出
    只適合當**輔助過濾**，不要拿它當事實。

    label 是 165 的官方標籤，可信度比內文推斷高很多，所以獨立餵進 category。
    """
    out: dict[str, list[str]] = {}
    for key in MULTI:
        hit = _hits(text, TABLES[key])
        if key == "category" and label:
            hit = _hits(label, CATEGORY) + hit
        out[key] = list(dict.fromkeys(hit))  # 去重又保留順序
    return out


def validate(values_by_key: dict[str, list[str]], code: str = "?") -> dict[str, list[str]]:
    """驗證手寫的 facets。給之後可能加進來的自撰案例卡用。

    derive_from_text() 走推斷，這支走驗證 —— 兩條路的產出形狀一樣，所以
    match() 不必知道某一筆的分類是推斷來的還是手寫的。
    """
    out: dict[str, list[str]] = {}
    for key in MULTI:
        values = values_by_key.get(key) or []
        if isinstance(values, str):  # 單值寫成字串也收，省得語料作者踩
            values = [values]
        table = TABLES[key]
        bad = [v for v in values if v not in table]
        if bad:
            raise UnknownFacet(
                f"案例 {code} 的 {key} 填了詞彙表裡沒有的值：{'、'.join(bad)}\n"
                f"  可用的值：{'、'.join(table)}\n"
                f"  要新增就去 facets.py 的 {key.upper()} 加一筆（連同問句裡會出現的"
                f"說法），否則使用者問不到它。"
            )
        out[key] = list(dict.fromkeys(values))
    return out


def known_values(items: list, key: str) -> list[str]:
    """語料裡實際出現過的值。給 health()、UI 的篩選選單與測試用。

    從資料長出來而不是直接讀詞彙表：詞彙表列得比語料多是刻意的
    （多列不會讓沒有的值憑空冒出來），但「現在真的有哪幾類」要看資料。
    """
    return sorted({v for it in items for v in (getattr(it, "facets", None) or {}).get(key, [])})


def parse_query(q: str, **_) -> dict[str, list[str]]:
    """從問句抓出過濾條件。

    規則式而不是叫模型抽：零延遲、可預測、出錯時看得出是哪條規則錯了。
    詞彙表跟 derive_from_text() 共用，所以建索引與查詢的分類不會對不起來。
    """
    f: dict[str, list[str]] = {}
    for key, table in TABLES.items():
        hit = _hits(q, table)
        if key == "channel":
            # 概括說法展開成具體管道，跟直接寫管道名的結果合併。
            # 用 dict.fromkeys 去重又保留順序 ——「社群或 FB 的詐騙」
            # 不該讓 Facebook 出現兩次。
            hit = hit + [c for group, chans in CHANNEL_GROUP.items() if group in q for c in chans]
        if hit:
            f[key] = list(dict.fromkeys(hit))
    return f


def terms_in(q: str, **_) -> list[str]:
    """問句裡實際出現的那幾個詞，照順序，**保留使用者原本打的字**。

    跟 parse_query 是一對，但用途相反：parse_query 回正規化之後的值
    （「社群」變成 Facebook／Instagram），給檢索用；這裡回原字，給「把省略式
    問句補成完整問句」用。

    為什麼一定要原字：來源那套量過，同樣的脈絡只換問句那一行，省略式的
    「那社群呢？」會答錯，補成使用者原本說法的完整問句才會對。補成展開後的
    值是另一句話，沒被驗證過。
    """
    words = [*CHANNEL_GROUP] + [w for table in TABLES.values() for v in table.values() for w in v]
    low = q.lower()
    found: dict[int, str] = {}  # 出現位置 → 原字；同一個位置只留最長的
    for w in words:
        i = low.find(w.lower())
        if i >= 0 and len(w) > len(found.get(i, "")):
            found[i] = q[i : i + len(w)]  # 切原字，大小寫照使用者寫的

    # 去掉被前一個詞蓋住的。
    #
    # ⚠ 這一段是必要的，因為詞彙表重疊：「假投資」與「投資」都在 CATEGORY 的
    #   同義詞裡，問「Facebook 上的假投資」會同時抽到「假投資」與「投資」，
    #   補出來的問句變成「Facebook、假投資、投資有哪些詐騙手法？」—— 那不是
    #   人話，而這條路的全部意義就是「補成一句讀得通的問句」。
    out: list[str] = []
    end = -1
    for i in sorted(found):
        if i < end:  # 起點還在上一個詞的範圍內＝被蓋住
            continue
        out.append(found[i])
        end = i + len(found[i])
    return out


def as_question(terms: list[str]) -> str | None:
    """把那些詞組成一個獨立可讀的問句。抽不出詞就回 None。

    「那怎麼辦？」這種沒有任何可比對的詞，這條路幫不上 —— 呼叫端要有別的
    退路（見 m5_agent.py 的重問）。
    """
    return "、".join(terms) + "有哪些詐騙手法？" if terms else None


LABEL = {"category": "類型", "channel": "接觸管道", "payment": "付款方式", "target": "常見對象"}


def describe(filters: dict[str, list[str]]) -> str:
    """把過濾條件講成一句人看得懂的話，要塞進 prompt 給模型看。

    為什麼需要：問「社群上有哪些詐騙」時 CHANNEL_GROUP 已經展開成
    Facebook／Instagram，而且篩出了全部符合的案例 —— 但模型不知道這件事。
    不說的話它會自己再判一次而且可能判錯（來源那套實測過同一個現象：
    模型不知道某個地名屬於某個區域，拿著符合的資料卻回「資料裡沒有」）。

    ⚠ 這不是「叫模型聽話」的咒語，是補一塊它沒有的知識。兩者的差別在於
      前者靠運氣，後者可以解釋為什麼會有效。
    """
    return "、".join(f"{LABEL[k]}＝{'／'.join(v)}" for k, v in filters.items() if k in LABEL and v)


def match(facets: dict[str, list[str]], filters: dict[str, list[str]]) -> bool:
    """一筆資料符不符合過濾條件。多值欄位只要有交集就算符合。"""
    for key, want in filters.items():
        if not set(want) & set(facets.get(key) or []):
            return False
    return True


_CODE = re.compile(r"^[A-Z]{3}-\d{2,}$")


def check_code(code: str) -> bool:
    """代號的格式。

    ⚠ 這不只是編號，是契約：m5_agent.py 的矛盾偵測靠「回答裡有沒有出現撈到
      的 code」判斷模型是不是在睜眼說瞎話。所以 code 必須短、好複述、而且
      **不會在別的句子裡碰巧出現** —— `INV-01` 這種形狀不會，但如果哪天改成
      用中文名稱當 code 就會。

      這件事值得寫下來，否則下一個人會覺得這個編號是多餘的裝飾而想拿掉。
    """
    return bool(_CODE.match(code or ""))
