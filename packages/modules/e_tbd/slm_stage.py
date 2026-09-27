"""S13 第一層：用地端小模型判「損失走到哪一步」。

規則層（`threads_stages.assess`）靠關鍵字與付款動詞判階段，在 gold v4 的
60 筆上是 46.7%（kappa 0.314）。它判不出來的地方很集中：**沒有付款動詞的
案例整批落到「尚未付款」**（語料 12.4%，gold 裡 12 筆有 10 筆人工標成別的），
因為受害者常寫「被騙了三萬」而不寫「我匯了三萬」。那是語意問題不是詞表問題，
再加詞只會誤傷。所以這一層交給模型讀。

## 模型只做抽取，階段由規則導出

第一版讓模型直接回答五選一，結果 **43.3%，比規則層還差**。混淆矩陣看得很清楚：
它把案子整批往嚴重的那端推（23 筆「已付款」有 8 筆判成「已交付帳戶控制權」、
20 筆「已交付」有 8 筆判成「帳戶遭盜用」，drained 的 precision 只有 0.25）。
判準寫的「先看帳戶，再看付款」被 3B 的模型讀成優先序，而「只算已經發生的，
不算對方要求或話術」那條壓不住它。

所以第二版改成：**模型只回答三個事實問題**（帳戶有沒有被別人動、有沒有交出
驗證資訊、自己匯出幾筆），階段由 `derive()` 照判準算出來。三個好處 ——

1. 抽取是小模型擅長的，五選一的分級判斷不是
2. 判準的優先序寫成程式碼，模型不能自己改順序
3. 判錯時看得出錯在哪個事實，而不是只知道「它選錯了」

問題也問得比判準更死：帳戶那題限定「案例明說有自己沒做的交易」，
交出那題限定「已經交了」（只是被要求不算）。

## 為什麼示範題是自己寫的，不是從語料裡挑

說明書 S13 要 12 則示範題。**示範題會進版控，而語料原文不能進版控**
（規畫書 §5，真實受害者陳述）。從語料挑就等於把原文寫進 repo。

所以 12 則全部是照判準自己寫的合成例，**不是任何一筆真實案例的改寫**，
也刻意避開 gold 那 60 筆的情節。代價是它們比真實敘述乾淨（真實案例會離題、
會把整段經過倒著講）；好處是這支檔案可以公開，別的模組要照做也有得抄。

挑選原則不是「每階各兩三則」，而是**照最容易錯的地方配**：被要求但沒照做
（3 則，第一版最大的錯源）、自己匯的錢 vs 帳戶被動用（3 則）、
正常網銀付款 vs 交出控制權（2 則）。排序刻意把嚴重的那幾則放中間，
不放結尾 —— 3B 的模型有近因偏誤，最後看到什麼就偏向答什麼。

## 格式約束用 "json" 不是 JSON Schema

`models.call_slm` 的 grammar 型別是 `str`，Ollama 的 format 欄位雖然吃
JSON Schema 物件，但傳字串進去它不會當 schema 解析。共用檔案不歸我改，
所以這裡走 `grammar="json"`（只保證是合法 JSON），欄位值合不合法在
`parse()` 自己驗 —— 驗不過就當這次失敗，交給 M4 的重試與退路。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from shared import models

STAGE_IDS = ("none", "paid", "repeated", "credentials", "drained")

STAGE_NAMES = {
    "none": "尚未付款",
    "paid": "已付款一次",
    "repeated": "已重複付款",
    "credentials": "已交付帳戶控制權",
    "drained": "帳戶遭盜用",
}

# 超過就頭尾各取一段。語料最長 901 字、8192 的 num_ctx 綽綽有餘，
# 這個上限是給「別的來源接進來」留的保險。中間截掉而不是截尾巴，
# 因為損失結果幾乎都寫在最後一段。
MAX_CHARS = 1500
HEAD_CHARS = 900
TAIL_CHARS = 500

QUESTIONS = """請只根據案例寫出來的事實回答三個問題。沒寫的就當作沒發生,不要推測。

1. stolen：案例有沒有明說**帳戶或信用卡出現自己沒有做的交易**
   (被盜刷、被盜轉、不明扣款、錢被轉走)? 有 true,沒有 false。
   自己匯出去的錢不算 —— 那是第 3 題。

2. handed：案例有沒有明說**已經把這些東西交給對方**,或**已經照對方指示操作**?
   驗證碼、簡訊碼、帳號密碼、身分證或證件照片、提款卡、無卡提款的取款碼,
   或是照對方指示按 ATM、做網銀認證、開啟遠端畫面。
   已經做了才算 true。**對方只是要求、自己沒照做,是 false。**

3. paid_times：自己主動匯出、轉出或付款**幾次**? 0、1、2(兩次以上)三選一。
   用網銀或 ATM 正常轉帳付款算在這題,不算第 2 題。
   付款方式不限轉帳 —— 無卡存款、把現金放到指定地點、超商代碼繳費都算。
   只寫結果不寫動作的也算:「被騙走三萬」「我損失了數千元」「遭詐騙五萬元」
   都是自己的錢出去了,算一次。
   **但「對方要求再匯、我沒有再匯」不算第二次。** 只數真的匯出去的。"""


@dataclass
class Shot:
    """一則示範。三個欄位是事實，階段由 derive() 從它們算出來。"""

    text: str
    stolen: bool
    handed: bool
    paid_times: int
    evidence: str


# 全部是照判準自己寫的合成例，不是真實案例。
SHOTS: list[Shot] = [
    Shot(
        "我在threads看到有人賣演唱會的票,私訊談好價錢後對方要我先匯訂金,"
        "我覺得怪怪的就沒匯,後來發現那個帳號已經關了。",
        False,
        False,
        0,
        "對方要我匯訂金,但我沒有匯。",
    ),
    Shot(
        "在threads看到轉讓的相機,加line談好後匯了8500元到對方給的帳戶,之後對方就不讀不回。",
        False,
        False,
        1,
        "匯了一筆8500元,帳戶沒有出現自己沒做的交易。",
    ),
    Shot(
        "threads上的賣家說要先掃他給的qr code扣款才能完成交易,我掃了之後頁面一直轉,"
        "他就一直催我重掃,我覺得不對勁沒有繼續操作。",
        False,
        False,
        0,
        "掃碼是對方要求的流程,款項沒有出去,也沒有交出任何驗證資訊。",
    ),
    Shot(
        "我在脆上跟一個賣家買包包,被騙走三萬多元,後來他說要我提供網銀帳號密碼才能辦退款,"
        "我沒有給就把他封鎖了。",
        False,
        False,
        1,
        "被騙走的三萬多是自己匯出去的,算一次;帳號密碼對方有要,但我沒有給。",
    ),
    Shot(
        "threads的賣家說要驗證我是不是真的買家,叫我把收到的簡訊驗證碼念給他,我念了,"
        "之後他就失聯了。",
        False,
        True,
        0,
        "簡訊驗證碼已經念給對方。",
    ),
    Shot(
        "對方在threads說我下單有問題,要我到atm按他說的步驟操作才能取消訂單,"
        "我照著按了一連串按鍵,自己也看不懂在做什麼。",
        False,
        True,
        0,
        "已經照對方指示操作 ATM。",
    ),
    Shot(
        "threads看到的賣場,結帳時填了信用卡資料,隔天收到三筆我沒有買過的國外刷卡簡訊,共五萬多。",
        True,
        True,
        0,
        "出現三筆自己沒有買過的刷卡紀錄;信用卡資料先前已經填給對方。",
    ),
    Shot(
        "對方以threads交易失敗為由要我提供網銀帳號密碼幫我處理,我給了之後,"
        "隔天發現帳戶裡的錢被分好幾筆轉走。",
        True,
        True,
        0,
        "帳戶裡的錢被別人分好幾筆轉走;網銀帳密先前已經交給對方。",
    ),
    Shot(
        "在脆上賣二手衣,買家說平台要求賣家先認證,要我拍身分證正反面和提款卡照片給他,我傳過去了。",
        False,
        True,
        0,
        "身分證與提款卡照片已經傳給對方。",
    ),
    Shot(
        "threads上買遊戲點數,先匯了1000元,對方說金額不對要我再匯2000元才會出貨,我照做了,"
        "結果東西沒收到人也消失。",
        False,
        False,
        2,
        "前後匯了兩筆。",
    ),
    Shot(
        "在脆裡跟對方買手機,我先匯了一筆訂金,對方說系統卡住要補保證金才能解凍,"
        "一直叫我再匯一次,我覺得不對就沒有再匯。",
        False,
        False,
        1,
        "只匯出訂金那一筆;保證金對方一直要,但我沒有匯。",
    ),
    Shot(
        "看到threads的貼文買球鞋,用網路銀行轉帳4200元給對方,轉完才發現那個賣場是假的。",
        False,
        False,
        1,
        "用網銀轉帳4200元是正常付款方式,只匯了一筆,沒有交出帳號密碼。",
    ),
]

_ALLOWED = set(STAGE_IDS)
_TRUE = {"true", "是", "有", "yes", "1"}
_FALSE = {"false", "否", "沒有", "無", "no", "0"}


@dataclass
class StageCall:
    """模型這一次的判讀。stage 是 derive() 算出來的，不是模型自己選的。"""

    stage: str
    reason: str
    stolen: bool
    handed: bool
    paid_times: int
    raw: str


def derive(*, stolen: bool, handed: bool, paid_times: int) -> str:
    """把三個事實照判準換成階段。順序就是標註者看到的那份判準的順序。

    ① 帳戶被別人動用 → drained　② 交出控制權 → credentials
    ③ 都沒有，看自己匯出幾筆 → repeated / paid / none

    這個順序是**判準**，不是調出來的參數 —— 拿 gold 那 60 筆去試哪種順序
    分數高，就是在測試集上配適，那樣量到的提升是假的。
    """
    if stolen:
        return "drained"
    if handed:
        return "credentials"
    if paid_times >= 2:
        return "repeated"
    if paid_times == 1:
        return "paid"
    return "none"


def _clip(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return f"{text[:HEAD_CHARS]}（中略）{text[-TAIL_CHARS:]}"


def _answer(shot: Shot) -> str:
    # evidence 排在三個事實前面：模型是照順序往下吐的，先講證據才輪到下判斷。
    # 反過來就變成先給答案再補理由 —— 第一版實測過，理由會跟答案自相矛盾
    # （「自己匯出三筆錢」配 stage=paid）。
    return json.dumps(
        {
            "evidence": shot.evidence,
            "stolen": shot.stolen,
            "handed": shot.handed,
            "paid_times": shot.paid_times,
        },
        ensure_ascii=False,
    )


def _shot_block() -> str:
    return "\n\n".join(f"案例：{s.text}\n答案：{_answer(s)}" for s in SHOTS)


def build_prompt(text: str) -> str:
    """三個事實問題 + 12 則示範 + 本案。示範順序固定（取樣是 greedy）。"""
    return (
        f"{QUESTIONS}\n\n"
        f"只輸出一個 JSON 物件,四個欄位,順序不能換:\n"
        f"  evidence    先寫。一句話,把案例裡決定上面三題的那些事實講出來,用繁體中文\n"
        f"  stolen      true 或 false\n"
        f"  handed      true 或 false\n"
        f"  paid_times  0、1 或 2\n\n"
        f"以下是 12 則示範:\n\n{_shot_block()}\n\n"
        f"照同樣格式回答這一則。\n\n案例：{_clip(text)}\n答案："
    )


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
    return None


def _as_times(value: object) -> int | None:
    if isinstance(value, bool):  # True 會被當成 1，那是錯的答案不是 1 次
        return None
    if isinstance(value, int | float):
        return min(2, max(0, int(value)))
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return min(2, max(0, int(match.group())))
    return None


def parse(raw: str) -> StageCall | None:
    """把模型吐的東西解成 StageCall。不合格就回 None，讓呼叫端決定重試或退路。

    `grammar="json"` 只保證是合法 JSON，不保證欄位在、型別對，所以驗證在這裡做。
    模型若把 JSON 包在一句話裡（小模型偶爾會），抓第一個大括號區段再解一次。
    """
    if not raw or not raw.strip():
        return None

    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    if not isinstance(data, dict):
        return None

    stolen = _as_bool(data.get("stolen"))
    handed = _as_bool(data.get("handed"))
    times = _as_times(data.get("paid_times"))
    if stolen is None or handed is None or times is None:
        return None

    return StageCall(
        stage=derive(stolen=stolen, handed=handed, paid_times=times),
        reason=str(data.get("evidence", "")).strip(),
        stolen=stolen,
        handed=handed,
        paid_times=times,
        raw=raw,
    )


def classify(text: str) -> StageCall | None:
    """呼叫一次模型。連不上或吐不合格的東西都會往上丟／回 None。

    重試次數不歸這裡決定 —— 那是 M4 三層退路的一部分，寫在 `m4_judgement.judge`。
    """
    raw = models.call_slm(build_prompt(text), grammar="json")
    return parse(raw)
