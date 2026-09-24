"""M3 語意檢索：做出「講白話也找得到」的搜尋（S12）。

完成型態是：關鍵字搜尋（BM25）跟語意搜尋同時跑 → RRF 合成排名 → 重排序模型重排前 50 筆。
現在是最簡可跑版本：純關鍵字重疊計分。等 S3 鎖定嵌入模型、S12 建好向量庫再換掉。

硬規則：沒有出處的結果不准回傳 —— 每一筆都要帶案例編號跟日期縣市。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from contracts import PackSpec, SimilarCase
from shared import deid, models

from .m1_corpus import Case, load_local

MODULE_DIR = Path(__file__).resolve().parent
INDEX_DIR = MODULE_DIR / "index"

# 語料池被關鍵字篩到比這個數還少時就不篩了。
#
# 篩選是為了把不相干的案例擋在向量比對之外，不是為了把召回砍光 —— 問句用的
# 詞剛好在語料裡很罕見時（例如只打了「穩賺不賠」，覆蓋 20.3%），硬篩會讓
# 向量層沒東西可排。留 top_k 的 4 倍當作可排序的餘裕。
_MIN_POOL_AFTER_FILTER_FACTOR = 4

# 第二道門檻的手法詞加分上限。
#
# 案例裡真的出現使用者打的那幾個手法詞時加分 —— 向量相似度看的是「整段話
# 像不像」，它分不出「像是因為都在講投資」還是「像是因為都在講出不了金」。
# 而後者才是使用者問的那件事。
#
# 全中（問句的手法詞案例裡都有）加滿 TACTIC_BONUS，半數就加一半，
# 依比例給 —— 用比例而不是「每命中一個加 0.05」，是為了讓問句長短不影響
# 加分的上限，否則打得越長的人分數被推得越高。
TACTIC_BONUS = 0.15

# 受災語彙：「我出事了」，但沒講到任何手法細節時用的詞。
#
# 為什麼需要這一組：光有平台詞不足以通過第一道門檻。「我在LINE上跟朋友
# 聊天」有平台詞、沒有手法詞，2026-09-21 實測它會拿回 5 筆假投資案例
# （底分 0.6674）—— 而那個分數比一個真正相關的查詢的第 2～5 名還高。
#
# 底分擋不住它：A 的語料整池都是 LINE 假投資，任何提到 LINE 的中文句子跟
# 整池的距離都差不多。實測純聊天 0.6140～0.6674、真受害者 0.7466、相關
# 查詢 0.6829，三組重疊，任何底分門檻都會連真的一起砍掉。
#
# 但「我在LINE上被騙了三萬元」要留住 —— 那是真的受害者，他只是還講不出
# 手法。所以另外收一組「出事了」的詞，跟 route_terms 分開：
# route_terms 回答的是「這像不像我這一類」（路由用，有區辨力數字），
# 這一組回答的是「這個人是不是來求助的」（檢索用）。
#
# ⚠️ 這份清單是判斷來的，不像 route_terms 有量過。
# TODO(S12)：20 題考題出來之後用它們校，特別是不含專有名詞的那 8 題。
# 🔴 只收「錢出事了」的詞，不收泛用的求助詞。
#    第一版收了「怎麼辦」跟「求助」，結果「我家的貓不吃飯了怎麼辦」照樣
#    拿回 5 筆假投資案例 —— 泛用求助詞不是受災訊號，是句型。
DISTRESS_TERMS = (
    "被騙",
    "受騙",
    "詐騙",
    "詐欺",
    "上當",
    "匯款",
    "匯了",
    "轉帳",
    "入金",
    "儲值",
    "拿不回",
    "要不回",
    "領不出",
    "提不出",
    "凍結",
    "報案",
    "165",
)


def _pack() -> PackSpec:
    return PackSpec.load(MODULE_DIR / "pack.yaml")


def _keywords() -> tuple[list[str], list[str]]:
    """回傳（平台詞，手法詞）。手法詞含 route_terms 與官方標籤用語。

    分開回傳是因為兩者在第一道門檻的職責不同，見 _gate_keyword()。
    """
    pack = _pack()
    platform = [t for t in pack.platform_terms if t]
    tactic = [t for t in list(pack.route_terms) + list(pack.labels_canon) if t]
    return platform, tactic


def _hits(terms: list[str], text: str) -> list[str]:
    return [t for t in terms if t in text]


def _tactic_bonus(text: str, q_tactic: list[str]) -> float:
    """第二道門檻的加分：案例裡提到幾個問句的手法詞。"""
    if not q_tactic:
        return 0.0
    hit = sum(1 for t in q_tactic if t in text)
    return TACTIC_BONUS * hit / len(q_tactic)


def _gate_keyword(query: str, pool: list[Case], top_k: int) -> tuple[list[Case], list[str]] | None:
    """第一道門檻：關鍵字。回 None 代表這題不該有答案。

    ① 問句一個平台詞、一個手法詞都沒命中 -> 回 None。
       擋的是「今天天氣如何」「我家的貓不吃飯」這種 —— 2026-09-21 實測，
       在沒有這道門檻的版本裡它們一樣拿回滿滿 5 筆案例，每一筆都帶著案例
       編號與縣市，看起來跟真的一模一樣。那比查不到更糟。

    ② 用問句命中的**手法詞**篩語料池。
       平台詞刻意不參與篩選：M1 切語料時就是用平台詞篩出來的，這 1,000 筆
       100% 都含平台詞（實測），拿它篩等於沒篩。

    ③ 篩完太少就不篩（見 _MIN_POOL_AFTER_FILTER_FACTOR）。
    """
    # 平台詞不再參與這道門檻 —— 「這題是不是 LINE 的案子」是路由（can_handle）
    # 的工作，檢索是路由決定交給 A 之後才跑的。這裡只問「他在講什麼事」。
    _platform, tactic = _keywords()
    q_tactic = _hits(tactic, query)
    q_distress = _hits(list(DISTRESS_TERMS), query)

    # 手法詞或受災語彙，兩者至少要有一個。
    #
    # 平台詞單獨不算：「我在LINE上跟朋友聊天」有平台詞但沒出事，正確答案
    # 是「沒有」。這一條是 2026-09-21 補的 —— 原本只要有平台詞就放行，
    # 純聊天會拿回 5 筆假投資案例。
    if not q_tactic and not q_distress:
        return None

    if not q_tactic:
        # 出事了但講不出手法（「我在LINE上被騙了三萬元」）—— 篩不動語料池，
        # 交給第二道門檻排序。這種人最需要看到相似案例。
        return pool, []
    narrowed = [c for c in pool if any(t in c.text for t in q_tactic)]
    if len(narrowed) < top_k * _MIN_POOL_AFTER_FILTER_FACTOR:
        return pool, q_tactic
    return narrowed, q_tactic


def _overlap_score(query: str, text: str) -> float:
    """最笨的相似度：字元 2-gram 重疊。這是 baseline，S12 要贏過它。"""
    if not query or not text:
        return 0.0
    grams_q = {query[i : i + 2] for i in range(len(query) - 1)}
    grams_t = {text[i : i + 2] for i in range(len(text) - 1)}
    if not grams_q:
        return 0.0
    return len(grams_q & grams_t) / len(grams_q)


VECTORS = INDEX_DIR / "vectors.npy"
CASE_IDS = INDEX_DIR / "case_ids.json"
META = INDEX_DIR / "meta.json"
# 斷點：編到一半的向量，與它對應的語料＋模型指紋。建完就刪掉。
CKPT_VECTORS = INDEX_DIR / "_build_vectors.npy"
CKPT_META = INDEX_DIR / "_build_meta.json"
EMBED_BATCH = 32
# 每編幾筆存一次斷點。1,000 筆在 bge-m3 上約 14 分鐘，存檔本身不到一秒。
CHECKPOINT_EVERY = 1000


def _fingerprint(cases: list[Case]) -> str:
    """語料 + 嵌入模型的指紋。任一邊變了，舊斷點就不能接著用。

    連內文一起雜湊（不是只有 case_id）—— 編號沒變但內文被重新清理過的話，
    接著用舊向量會得到一份對不上自己語料的索引，而且不會有任何徵兆。
    """
    lock = models.MODEL_LOCK["embedding"]
    h = hashlib.sha256()
    h.update(f"{lock.name}@{lock.revision}\n".encode())
    for c in cases:
        h.update(c.case_id.encode())
        h.update(b"\0")
        h.update(c.text.encode())
        h.update(b"\0")
    return h.hexdigest()


def _save_checkpoint(arr, done: int, fp: str) -> None:
    """先寫暫存檔再 rename —— 存到一半被砍掉不會把既有斷點弄壞。"""
    import numpy as np

    tmp = CKPT_VECTORS.with_suffix(".tmp.npy")
    np.save(tmp, arr[:done])
    os.replace(tmp, CKPT_VECTORS)
    CKPT_META.write_text(
        json.dumps({"done": done, "fingerprint": fp}, ensure_ascii=False), encoding="utf-8"
    )


def _load_checkpoint(fp: str, total: int):
    """回（已編碼的向量, 已完成筆數）。沒斷點或對不上就回 (None, 0)。"""
    if not (CKPT_VECTORS.exists() and CKPT_META.exists()):
        return None, 0
    try:
        meta = json.loads(CKPT_META.read_text(encoding="utf-8"))
        if meta.get("fingerprint") != fp:
            print("  斷點的語料或模型跟現在對不上 —— 整個重編", flush=True)
            return None, 0
        import numpy as np

        part = np.load(CKPT_VECTORS)
    except Exception as exc:  # 斷點壞了不該讓重建整個失敗，重編就是了
        print(f"  斷點讀不起來（{exc}）—— 整個重編", flush=True)
        return None, 0
    done = min(int(meta.get("done", 0)), len(part), total)
    if done <= 0:
        return None, 0
    return part[:done], done


def build_index(*, batch: int = EMBED_BATCH, checkpoint_every: int = CHECKPOINT_EVERY) -> int:
    """建自己的向量庫（make index MODULE=…）。

    向量庫是你自己建的，不進版控（幾百 MB）—— 別人要重現時自己跑這行。

    案例整筆不切塊：165 的敘述中位數 236 字，本來就在一個切塊的長度內，
    硬切反而會把「先加 LINE、再匯款」這種前後關係拆掉。

    存成 numpy 檔而不是 Chroma / Qdrant：那兩個都不在專案相依裡，而
    pyproject.toml 是凍結的共管路徑。真的要換是 S12 的決定（已評估，見
    docs/向量庫選型.md）。

    **斷點續建**：每 checkpoint_every 筆把編好的部分存進 _build_vectors.npy，
    中斷之後再跑同一行就從那裡接下去；語料或嵌入模型換了會自動作廢重編
    （靠 _fingerprint 比對）。全部編完才寫出 vectors.npy —— 檢索端永遠
    不會讀到編到一半的索引。

    2026-09-24 加的。語料從 17,764 筆改成 81,423 筆之後一次要編 19 小時
    （bge-m3 實測 1.17 筆/秒），中途掉電從頭來過的代價太高。同一次也把
    「先收進 list 再轉 numpy」改成預先配置陣列：81,423 × 1024 個 Python
    float 物件要 2 GB 以上，而 float32 陣列只要 334 MB。

    TODO(S12)：法規文件要切塊（每 300–500 字一段、前後重疊 50 字），
               案例維持整筆。
    """
    cases = load_local()
    if not cases:
        raise FileNotFoundError("還沒有自己的語料。先跑 M1（S9）切出自己那一份。")

    import numpy as np

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    total = len(cases)
    fp = _fingerprint(cases)
    part, done = _load_checkpoint(fp, total)

    arr = None
    if part is not None:
        arr = np.zeros((total, part.shape[1]), dtype="float32")
        arr[:done] = part
        print(f"  從斷點接下去：已完成 {done:,}/{total:,}", flush=True)

    resumed_at = done
    started = time.monotonic()
    saved_at = done
    try:
        for i in range(done, total, batch):
            chunk = cases[i : i + batch]
            vecs = models.embed([c.text for c in chunk])
            if arr is None:
                arr = np.zeros((total, len(vecs[0])), dtype="float32")
            arr[i : i + len(vecs)] = np.asarray(vecs, dtype="float32")
            done = i + len(vecs)
            if done - saved_at >= checkpoint_every:
                _save_checkpoint(arr, done, fp)
                saved_at = done
            elapsed = time.monotonic() - started
            rate = (done - resumed_at) / elapsed if elapsed > 0 else 0.0
            eta = (total - done) / rate / 3600 if rate > 0 else 0.0
            print(
                f"  已編碼 {done:>6,}/{total:,}　{rate:.2f} 筆/秒　預計還要 {eta:.1f} 小時",
                flush=True,
            )
    except (KeyboardInterrupt, Exception):
        if arr is not None and done > saved_at:
            _save_checkpoint(arr, done, fp)
        print(
            f"  中斷了。斷點停在 {done:,}/{total:,}，再跑一次同一行會從這裡接下去",
            flush=True,
        )
        raise

    # 先正規化，之後比對就是單純的內積，省一次除法也少一處出錯的地方
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    np.save(VECTORS, arr)
    CASE_IDS.write_text(
        json.dumps([c.case_id for c in cases], ensure_ascii=False), encoding="utf-8"
    )
    lock = models.MODEL_LOCK["embedding"]
    META.write_text(
        json.dumps(
            {
                "count": total,
                "dim": int(arr.shape[1]),
                "model": f"{lock.name}@{lock.revision}",
                "fingerprint": fp,
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    CKPT_VECTORS.unlink(missing_ok=True)
    CKPT_META.unlink(missing_ok=True)
    return total


# 向量庫的記憶體快取。key 是檔案的 mtime 與大小 —— 重建過就自動失效。
_INDEX_CACHE: tuple[tuple[int, int, int], object, list[str]] | None = None
# 索引過期的警告一次就夠，不要每次查詢都印
_WARNED_STALE = False


def _load_index():
    """讀向量庫並快取起來。

    81,423 × 1024 的 float32 是 334 MB，每次查詢都重讀會吃掉 1 秒延遲預算的
    一大半（語料還是 17,764 筆、69 MB 時看不出來，所以原本每次都重讀）。
    """
    global _INDEX_CACHE
    import numpy as np

    vs, cs = VECTORS.stat(), CASE_IDS.stat()
    stamp = (vs.st_mtime_ns, vs.st_size, cs.st_mtime_ns)
    if _INDEX_CACHE is not None and _INDEX_CACHE[0] == stamp:
        return _INDEX_CACHE[1], _INDEX_CACHE[2]
    arr = np.load(VECTORS)
    ids = json.loads(CASE_IDS.read_text(encoding="utf-8"))
    _INDEX_CACHE = (stamp, arr, ids)
    return arr, ids


def _vector_scores(query: str, pool: list[Case]) -> dict[str, float] | None:
    """有向量庫就用，沒有就回 None 讓呼叫端退回字元重疊。"""
    global _WARNED_STALE
    if not (VECTORS.exists() and CASE_IDS.exists()):
        return None
    try:
        import numpy as np

        arr, ids = _load_index()
        if len(ids) != arr.shape[0]:
            return None
        # 索引蓋不到現在的語料 —— 多半是改了語料還沒重建。
        #
        # 這一關不能省：search() 拿分數的寫法是 by_id.get(case_id, 0.0)，
        # 沒蓋到的案例會安靜地拿 0 分，看起來像「檢索變笨」而不像「壞掉」。
        # 寧可整批退回字元重疊 —— 至少全部案例用的是同一把尺。
        known = set(ids)
        if any(c.case_id not in known for c in pool):
            if not _WARNED_STALE:
                missing = sum(1 for c in pool if c.case_id not in known)
                print(
                    f"  [警告] 向量庫少了 {missing:,} 筆語料的向量，這次起退回字元重疊。"
                    f"請重跑 make index MODULE=a_tbd",
                    flush=True,
                )
                _WARNED_STALE = True
            return None
        q = np.asarray(models.embed([query])[0], dtype="float32")
        q /= np.linalg.norm(q)
        sims = arr @ q
    except Exception:
        # 向量庫壞了或模型叫不動都不該讓檢索整個掛掉 —— 退回字元重疊
        return None
    return dict(zip(ids, (float(x) for x in sims), strict=False))


def search(query: str, *, top_k: int = 5, cases: list[Case] | None = None) -> list[SimilarCase]:
    """檢索相似案例。回傳的每一筆都帶案例編號，節錄一律先去識別化。"""
    pool = cases if cases is not None else load_local()
    if not pool:
        return []

    # ── 第一道門檻：關鍵字 ──────────────────────────────────────
    # 問句跟這個模組的平台 × 手法完全沾不上邊時，正確答案是「沒有」，
    # 不是「最接近的五筆」。
    gated = _gate_keyword(query, pool, top_k)
    if gated is None:
        return []
    pool, q_tactic = gated

    # ── 第二道門檻：相似度 + 手法詞加分 ─────────────────────────
    # 底分還是向量相似度（沒有向量庫就退回字元重疊，檢索不會整個失效，
    # 只是變笨），案例裡真的提到使用者打的手法詞就往上加。
    #
    # 為什麼要加這一層：向量相似度只看「整段話像不像」，分不出「像是因為
    # 都在講投資」還是「像是因為都在講出不了金」—— 而後者才是使用者問的
    # 那件事。加分把「真的講到同一件事」的案例往前推。
    by_id = _vector_scores(query, pool)
    if by_id is not None:
        base = {c.case_id: by_id.get(c.case_id, 0.0) for c in pool}
    else:
        base = {c.case_id: _overlap_score(query, c.text) for c in pool}
    scored = sorted(
        ((base[c.case_id] + _tactic_bonus(c.text, q_tactic), c) for c in pool),
        key=lambda pair: pair[0],
        reverse=True,
    )

    out: list[SimilarCase] = []
    for score, case in scored[:top_k]:
        if score <= 0:
            continue
        safe = deid.mask(case.text)
        out.append(
            SimilarCase(
                case_id=case.case_id,
                source=case.source,
                excerpt=safe.text[:120],
                date=case.date or None,
                county=case.county or None,
                score=round(min(1.0, score), 3),
                label=case.label or None,
            )
        )
    return out


def reciprocal_rank_fusion(*rankings: list[str], k: int = 60) -> list[str]:
    """RRF：兩邊都排前面的，最後就排前面。S12 要用它合成 BM25 與向量搜尋。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]
