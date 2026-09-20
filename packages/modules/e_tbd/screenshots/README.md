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

E 負責求職平台 × 人頭帳戶。說明書 S11 對 E 的描述：
「職缺頁、社團貼文、LINE 面試對話。要抓職缺的結構化欄位，跟『請提供存摺照片』這類訊息」。

| # | 檔名 | layout | 變化 | is_scam | 畫面內容 |
|---|---|---|---|:--:|---|
| 01 | `e01_jobpost_normal` | ad_post | light / sharp | ✗ | 人力銀行正常職缺（對照組）|
| 02 | `e02_jobpost_suspicious` | ad_post | light / sharp | ✓ | 日領高薪、免經驗、無公司登記 |
| 03 | `e03_jobpost_detail` | ad_post | light / sharp | ✓ | 職缺詳情頁，結構化欄位齊全 |
| 04 | `e04_fbgroup_post` | ad_post | light / sharp | ✓ | FB 打工社團招募貼文 |
| 05 | `e05_fbgroup_dark` | ad_post | **dark** / sharp | ✓ | 同上，深色模式 |
| 06 | `e06_jobpost_lowres` | ad_post | light / **low_res** | ✓ | 低解析度的職缺截圖 |
| 07 | `e07_chat_intro` | chat | light / sharp | ✗ | LINE 面試開場（還看不出問題）|
| 08 | `e08_chat_id_request` | chat | light / sharp | ✓ | 要求身分證正反面 |
| 09 | `e09_chat_passbook` | chat | light / sharp | ✓ | **「請提供存摺封面照片」** |
| 10 | `e10_chat_card_mail` | chat | light / sharp | ✓ | 要求店到店寄出提款卡 |
| 11 | `e11_chat_netbank` | chat | light / sharp | ✓ | 詢問網銀帳號密碼與驗證碼 |
| 12 | `e12_chat_dark` | chat | **dark** / sharp | ✓ | 深色模式對話 |
| 13 | `e13_chat_group` | chat | light / sharp | ✓ | 多人工作群組（多發話人）|
| 14 | `e14_chat_blurred` | chat | light / **blurred** | ✓ | 手震拍糊的對話 |
| 15 | `e15_chat_withdraw` | chat | light / sharp | ✓ | 指示提領現金交付 |
| 16 | `e16_sms_interview` | sms | light / sharp | ✓ | 簡訊：面試通知 |
| 17 | `e17_sms_bank_alert` | sms | light / sharp | ✗ | 簡訊：銀行帳戶警示通知 |
| 18 | `e18_profile_recruiter` | profile | light / sharp | ✓ | 招募者的個人檔案（空殼）|
| 19 | `e19_shipping_receipt` | product | light / **low_res** | ✓ | 超商店到店寄件單據 |
| 20 | `e20_company_page` | ad_post | light / sharp | ✓ | 求職網站上的空殼公司介紹頁 |

**分布**：ad_post 7 張、chat 8 張、sms 2 張、profile 1 張、product 1 張，
外加 1 張 ad_post 對照組。深色 2 張、低解析度 2 張、模糊 1 張，其餘清晰。
對照組 3 張（01、07、17）—— 17 是真實的銀行通知，看起來很像詐騙但不是。

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
