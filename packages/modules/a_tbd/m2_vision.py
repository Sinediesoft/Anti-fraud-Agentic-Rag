"""M2 截圖理解：讓系統看懂你那個平台的截圖（S11）。

這一步完全自己寫，沒有共用工具可以用。
輸出是「帶版面角色的文字」：哪一種畫面、文字分成哪些塊、對話裡哪句是誰說的。

降級規則：認不出版面時退回「只輸出文字、不標誰說的」，絕不整個當掉。

## 引擎：rapidocr 3.9.2 內建的 PP-OCRv6 small（2026-09-23 選定）

模型本身是 PaddleOCR（百度）的 PP-OCR 系列：v4／v5／v6 是 PaddleOCR 的模型世代，
不是 rapidocr 的版本。RapidOCR 不訓練模型，它把 PaddleOCR 的官方模型轉成 ONNX，
再配上前後處理的推論流程。兩邊都是 Apache-2.0，轉出來的 ONNX 沿用同樣的授權。
寫報告時來源要分開寫：模型是 PaddleOCR，推論引擎與 ONNX 轉換是 RapidOCR。

下面的數字是 RapidOCR 的 ONNX 版本在我們的截圖上量的。PaddleOCR 官方文件的數字
（例如繁體辨識準確率 v4 mobile 0.32 -> v5 mobile 0.72）用的是原版模型與它自己的
流程，前後處理的實作與參數都不同，兩邊不能直接比。

用 e_tbd 那 20 張有標註的截圖（標註與畫面同源，共 1,430 字）實測，錯字率用
shared.eval.corpus_cer 算，在 A 的 M5 上各自單獨跑：

    模型                        錯字率   720px 一張   手機原尺寸   行程記憶體
    PP-OCRv4 mobile（原本）      0.129      191 ms       496 ms      1.70 GB
    PP-OCRv5 mobile              0.031      179 ms       475 ms      1.71 GB
    PP-OCRv6 small   <- 選這個    0.019      217 ms       544 ms      1.57 GB
    PP-OCRv6 medium              0.016      933 ms     2,481 ms      3.17 GB

v4 錯的幾乎都是把繁體認成簡體（帳→帐、徵→微、內→内、戶→户），路由詞「提領」
就這樣比對不到。v6 small 是最準的實用款，而且模型檔包在套件裡 —— 不用連網、
不用另外釘檔案，套件版本釘住就等於模型釘住（見 pyproject.toml 的 ocr 那組）。

🔴 rapidocr 相依 requests：設定沒給 model_path 的模型不在本機時，它會自己去
   ModelScope 下載。所以三顆模型一律明確指向套件內建的檔案 —— 截圖理解這一步
   永遠不連網，原文不離開使用者的電腦。

## 記憶體與速度

引擎在第一張圖進來時才載入（實測約 0.2 秒），之後整個 process 共用一份。
跑過幾十張圖後行程記憶體停在約 1.5 GB 不再長，那是 onnxruntime 處理圖片時長出來
的，不是模型本身（模型檔才 30 MB）。

路由每一輪都會問每個模組 can_handle()，外殼每次送出又會把同一張圖再加一次 ——
沒有快取的話，一段對話同一張圖會被辨識十幾次（手機截圖一張約 0.5 秒）。所以
辨識結果用圖檔內容的雜湊快取，內容一樣的圖只辨識一次。
"""

from __future__ import annotations

import collections
import hashlib
import importlib.metadata
import importlib.util
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path

from contracts import ImageInput


@dataclass
class TextBlock:
    """截圖上的一塊文字。"""

    text: str
    role: str = "unknown"  # title / body / button / price / bubble / field
    speaker: str = "unknown"  # self / other / system —— 對話類才有意義
    bbox: tuple[int, int, int, int] | None = None


@dataclass
class ScreenRead:
    """一張截圖看懂之後的結果。"""

    layout: str = "unknown"  # ad_post / chat / sms / product / checkout / profile
    blocks: list[TextBlock] = field(default_factory=list)
    degraded: bool = False
    reason: str = ""

    @property
    def plain_text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text.strip())


RAPIDOCR_VERSION = "3.9.2"
OCR_ENGINE = f"rapidocr {RAPIDOCR_VERSION} / PP-OCRv6 small"
_PACKAGE = "rapidocr"
# 套件內建的三顆模型。檔名跟著 RAPIDOCR_VERSION 走，換版本要一起確認。
# 方向分類用的是 v2.0 mobile —— 跟 PP-OCRv4 那套同一顆，rapidocr 的預設也是它。
_DET_MODEL = "PP-OCRv6_det_small.onnx"
_REC_MODEL = "PP-OCRv6_rec_small.onnx"
_CLS_MODEL = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"
_INSTALL_HINT = "裝法：uv sync --extra ocr"

_ENGINE = None
# 鍵是圖檔內容的 SHA-256，不是路徑：外殼把上傳檔寫到「暫存目錄/原檔名」，
# 兩次上傳同名的不同截圖會落在同一個路徑。值存成不可變的 tuple，每次再組新的
# TextBlock —— 之後判斷發話者的程式會改 block.speaker，不能改到快取裡那份。
_CACHE: collections.OrderedDict[str, tuple[tuple[str, tuple[int, int, int, int]], ...]] = (
    collections.OrderedDict()
)
_CACHE_MAX = 64
# 載入與辨識都上鎖：多個分頁會同時送圖，引擎只能載一份。
_LOCK = threading.Lock()


class OCRUnavailable(RuntimeError):
    """這台不能跑 OCR（沒裝、或版本不對）。這是預期內的狀態：降級，不是錯誤。"""


def _models_dir() -> Path | None:
    spec = importlib.util.find_spec(_PACKAGE)  # 只找不 import，health() 每次開畫面都會問
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations))) / "models"


def engine_status() -> tuple[bool, str]:
    """OCR 能不能用，以及不能用時下一步該做什麼。只查套件與模型檔，不載模型。"""
    models_dir = _models_dir()
    if models_dir is None:
        return False, f"這台沒裝 OCR（rapidocr），有截圖時只用打字的內容判讀。{_INSTALL_HINT}"
    try:
        installed = importlib.metadata.version(_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        installed = "?"
    if installed != RAPIDOCR_VERSION:
        # 模型檔包在套件裡，版本不同就是模型不同 —— 跟 MODEL_LOCK 比對 digest 同一個道理
        return False, (
            f"rapidocr 是 {installed}，鎖定的是 {RAPIDOCR_VERSION}，版本不同模型就不同。"
            f"{_INSTALL_HINT}"
        )
    missing = [n for n in (_DET_MODEL, _REC_MODEL, _CLS_MODEL) if not (models_dir / n).is_file()]
    if missing:
        return False, f"rapidocr 內建的模型檔不見了：{'、'.join(missing)}。重裝：{_INSTALL_HINT}"
    return True, OCR_ENGINE


def _make_engine():
    ok, detail = engine_status()
    if not ok:
        raise OCRUnavailable(detail)
    # 延遲 import：沒裝 ocr 那組的人照樣 import 得了這個模組，只是截圖會降級
    from rapidocr import RapidOCR

    models_dir = _models_dir()
    return RapidOCR(
        params={
            "Global.log_level": "error",
            "Det.model_path": str(models_dir / _DET_MODEL),
            "Rec.model_path": str(models_dir / _REC_MODEL),
            "Cls.model_path": str(models_dir / _CLS_MODEL),
        }
    )


def _bbox(box) -> tuple[int, int, int, int]:
    """rapidocr 的文字框是四個角 -> (左上 x, 左上 y, 右下 x, 右下 y)。斜的框取外接矩形。"""
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys))


def _recognise(key: str, data: bytes) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
    global _ENGINE
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached
        if _ENGINE is None:
            _ENGINE = _make_engine()
        result = _ENGINE(data)
        boxes = result.boxes if result.boxes is not None else ()
        lines = tuple(
            (text, _bbox(box)) for text, box in zip(result.txts or (), boxes, strict=True)
        )
        # 只快取成功的結果：偶發的失敗若被記住，這張圖在這個 process 裡就再也讀不到了
        _CACHE[key] = lines
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        return lines


def _degraded(reason: str) -> ScreenRead:
    return ScreenRead(layout="unknown", blocks=[], degraded=True, reason=reason)


def _load(image: ImageInput) -> tuple[str, bytes] | None:
    """讀圖檔並算出快取鍵。讀不到回 None。"""
    try:
        data = Path(image.path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest(), data


def _screen(image: ImageInput, loaded: tuple[str, bytes] | None) -> ScreenRead:
    if loaded is None:
        name = image.filename or Path(image.path).name
        return _degraded(f"截圖讀不到（{name}），這次只用打字的內容判讀。")
    try:
        lines = _recognise(*loaded)
    except OCRUnavailable:
        return _degraded("這台沒有截圖辨識，這次只用打字的內容判讀。")
    except Exception as exc:  # 引擎壞掉要降級，不是整條流程當掉
        return _degraded(f"截圖辨識失敗（{type(exc).__name__}），這次只用打字的內容判讀。")
    return ScreenRead(layout="unknown", blocks=[TextBlock(text=t, bbox=b) for t, b in lines])


def read_screenshot(image: ImageInput) -> ScreenRead:
    """把一張截圖變成帶版面角色的文字。

    TODO(S11) 這支要做的三件事：
      1. 判斷這是哪一種畫面                     ← 還沒做，layout 一律 unknown
      2. 把文字抓出來並依版面分塊                ← 抓文字做了（一行一塊，帶座標）；依版面分塊還沒
      3. 對話類的要判斷哪句是對方說的、哪句是自己說的
         （靠氣泡在左邊還右邊、背景色）        ← 還沒做，speaker 一律 unknown
    沒做的部分照檔頭的降級規則：只輸出文字、不標誰說的。
    門檻：版面判對率 ≥ 0.85、文字錯誤率 ≤ 0.15。
    """
    return _screen(image, _load(image))


def read_all(images: list[ImageInput]) -> list[ScreenRead]:
    """每張圖讀一次。內容一模一樣的只算一張 —— 外殼每次送出都會把同一張圖再加一次，
    重複的字只會把檢索的問句塞滿同樣的內容。"""
    out: list[ScreenRead] = []
    seen: set[str] = set()
    for image in images:
        loaded = _load(image)
        if loaded is not None:
            if loaded[0] in seen:
                continue
            seen.add(loaded[0])
        out.append(_screen(image, loaded))
    return out
