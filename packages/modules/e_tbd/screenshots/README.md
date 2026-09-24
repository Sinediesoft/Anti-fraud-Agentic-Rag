# 模組 E 的模擬截圖與標註（S11）

> **⚠ 這份格式需要全隊確認。** 說明書 S11 注意事項：
> 「五個人的截圖標註格式要在 W3 就講好，不然五份合不起來，第六週沒辦法合併評估」。
>
> 目前 `contracts/` 沒有截圖相關的型別，`evaluate.py` 也還沒有截圖評估，
> A 的 `screenshots/` 同樣是空的 —— 也就是這件事還沒有人做。
> 這份是 E 提出的第一版，**其他四個人請直接在 PR 上提意見**，
> 談定之後應該往上搬到 `contracts/` 或 `docs/`，不該留在單一模組裡。

## 為什麼不用真實截圖

說明書 S11：「**絕對不要用真實受害者的截圖。全部自己做，這是倫理底線，也要寫進報告**」。

這裡 20 張全部是用 HTML 排版後截圖產生的，原始碼放在 `_sources/`，
所以任何人都能重現、也能檢驗我們沒有用到真實資料。

## 標註檔格式

`annotations.yaml`，一張截圖一筆。

```yaml
format_version: 1
module: e_tbd
annotator: c85016921920-design

screenshots:
  - file: e01_jobpost_normal.png
    layout: ad_post          # 版面判對率的正確答案
    variant:                 # 這張刻意帶的變化，用來看模型在什麼條件下會壞
      width: 1080
      theme: light           # light / dark
      quality: sharp         # sharp / blurred / low_res
    is_scam: false           # 對照組也要有，不然模型會學成「只要是職缺就是詐騙」
    blocks:
      - text: 誠徵 行政助理
        role: title          # title / body / button / price / bubble / field
        speaker: null        # self / other / system；非對話類填 null
    notes: 正常職缺，對照組
```

### 三個欄位的值域

直接沿用 `m2_vision.py` 裡 `ScreenRead` / `TextBlock` 的註解，不另創一套：

| 欄位 | 值域 | 用途 |
|---|---|---|
| `layout` | `ad_post` / `chat` / `sms` / `product` / `checkout` / `profile` | 版面判對率（門檻 ≥ 0.85）|
| `role` | `title` / `body` / `button` / `price` / `bubble` / `field` | 版面分塊 |
| `speaker` | `self` / `other` / `system` / `null` | 對話角色判定 |

`blocks[].text` 合起來就是這張圖的 ground truth 全文，用來算 CER（門檻 ≤ 0.15）。

### 兩個 E 自己加的欄位，提出來討論

1. **`is_scam`** —— 20 張裡要有對照組（正常的職缺頁）。只餵詐騙樣本，
   模型會學成「看到職缺就報警」，而 `can_handle()` 要保守是說明書 S4 的明確要求。
2. **`variant`** —— 說明書要求截圖要有變化（不同解析度、深色淺色、故意拍糊），
   但如果不記下來是哪一種變化，之後就只知道「錯了」，不知道「在什麼條件下會錯」。

這兩個欄位其他四組應該也用得上，但如果大家覺得不需要，E 自己留著也行 ——
只要 `layout` / `role` / `speaker` 三個對齊，合併評估就不會出問題。

## E 的 20 張清單

E 負責 **Threads × 網路購物詐騙**。說明書 S11 對 E 的原始描述是針對舊題目
（「職缺頁、社團貼文、LINE 面試對話」）寫的，題目 2026-09-21 改定之後
這 20 張在 **09-23 全部重做**，改依 S10 的 60 筆標準答案歸納出的劇本：

> Threads 貼文 → 私訊改加 LINE → 假賣貨便連結 → 「未完成實名制認證」
> → 假客服 → 匯款（常重複多筆）→ 網銀帳密／分享螢幕／付款碼
> → 少數走到寄金融卡、無卡提款

五個 Harm 階段（none／paid／repeated／credentials／drained）每一段都要有畫面。
版型、三張對照組、變化組合沿用舊版設計 —— 那部分跟題目無關。

| # | 檔名 | layout | 變化 | is_scam | 畫面內容 |
|---|---|---|---|:--:|---|
| 01 | `e01_post_normal` | ad_post | light / sharp | ✗ | 正常的二手轉售貼文（對照組）|
| 02 | `e02_post_underpriced` | ad_post | light / sharp | ✓ | 價格明顯低於行情、限時限量、要求私訊 |
| 03 | `e03_product_page` | ad_post | light / sharp | ✓ | 欄位齊全的商品頁但賣家資訊空泛 |
| 04 | `e04_post_ticket` | ad_post | light / sharp | ✓ | 熱門票券現貨，急迫話術加只收匯款 |
| 05 | `e05_post_dark` | ad_post | **dark** / sharp | ✓ | 同類貼文的深色模式 |
| 06 | `e06_post_lowres` | ad_post | light / **low_res** | ✓ | 低解析度（轉傳好幾手的圖）|
| 07 | `e07_chat_normal` | chat | light / sharp | ✗ | 正常的議價與約面交（對照組）|
| 08 | `e08_chat_to_line` | chat | light / sharp | ✓ | 導流到 LINE |
| 09 | `e09_chat_kyc_block` | chat | light / sharp | ✓ | **「未完成實名制認證」** |
| 10 | `e10_chat_repeat_pay` | chat | light / sharp | ✓ | 以格式錯誤為由要求再匯一次 |
| 11 | `e11_chat_netbank` | chat | light / sharp | ✓ | 索取網銀帳密與驗證碼 |
| 12 | `e12_chat_dark` | chat | **dark** / sharp | ✓ | 深色模式（要求遠端與分享畫面）|
| 13 | `e13_chat_group` | chat | light / sharp | ✓ | 多人群組的假買家見證（多發話人）|
| 14 | `e14_chat_blurred` | chat | light / **blurred** | ✓ | 手震拍糊（ATM 操作指示）|
| 15 | `e15_chat_paycode` | chat | light / sharp | ✓ | 要求出示付款碼並一筆一筆扣 |
| 16 | `e16_sms_order_alert` | sms | light / sharp | ✓ | 簡訊：訂單異常通知帶短網址 |
| 17 | `e17_sms_bank_alert` | sms | light / sharp | ✗ | 簡訊：真的銀行通知（對照組）|
| 18 | `e18_profile_seller` | profile | light / sharp | ✓ | 賣家檔案：剛建立、無評價 |
| 19 | `e19_shipping_receipt` | product | light / **low_res** | ✓ | 賣家傳來的寄件單據，低解析度翻拍 |
| 20 | `e20_kyc_page` | ad_post | light / sharp | ✓ | 假的實名認證頁（釣魚）|

**分布**：ad_post 7 張、chat 9 張、sms 2 張、profile 1 張、product 1 張。
深色 2 張、低解析度 2 張、模糊 1 張，其餘清晰。
對照組 3 張（01、07、17）—— 17 是真實的銀行通知，看起來很像詐騙但不是。

> ⚠ **`render.py` 有三個曾經讓壞圖混進來的坑**（全都回傳碼 0、stderr 空白）：
> 缺 `--virtual-time-budget` 會截到空白；Edge 的 launcher 會把工作丟給背景程序
> 就自己退出，殘留程序累積後整批失敗，而且遲到的寫入會把好圖蓋成錯誤頁；
> 導航失敗時會把 `ERR_FILE_NOT_FOUND` 錯誤頁截給你（有效 PNG、大小正常）。
> 現在改成截暫存檔 → 等寫完 → **驗證圖裡有沒有該主題的底色** → 才複製過來，
> 五次都不對就中斷整批。詳見 `_sources/render.py` 的註解。

## 重現方式

```bash
uv run python packages/modules/e_tbd/screenshots/_sources/render.py
```

`_sources/shots.yaml` 是唯一的資料來源，`render.py` 讀它產生 PNG，
**同時產生 `annotations.yaml`**。改 YAML 重跑，圖跟標註會一起更新，
所以兩者不可能對不上。

產出：20 張、合計 366 KB（說明書 §1.6 的上限是 10 MB）。

> `render.py` 放在自己的模組裡，沒有動 `tools/`。如果其他四個人覺得好用，
> 可以搬到 `tools/` 變成全隊共用 —— 但那要動共用區，需要全員同意。

## 這個做法的侷限（要寫進報告）

說明書 S11：「自建集只有 100 張、規模小可能有偏差，報告裡要誠實揭露」。
除了規模，用 HTML 渲染還有兩個特有的偏差：

1. **文字是渲染出來的，不是拍攝的。** 沒有攝影雜訊、摩爾紋、螢幕反光、
   JPEG 壓縮痕跡。OCR 在這種圖上的錯字率會比真實情況樂觀，
   **CER 的數字不能直接當成上線後的預期表現**。
2. **版型是我自己畫的，不是真的 App。** 氣泡形狀、間距、配色是仿的，
   跟真正的 LINE 或人力銀行 App 有差距。版面判對率同樣偏樂觀。

`quality: blurred` 與 `low_res` 兩種變化是為了部分補償第 1 點，
但補不完。真要知道實際表現，得用真的手機截圖測 —— 而那必須是自己拍的，
不能用受害者的。
