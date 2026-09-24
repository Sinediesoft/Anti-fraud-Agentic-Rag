"""模組 E：Threads × 網路購物詐騙（語料 4,140 筆）。

題目 2026-09-21 從說明書 §0.2 的「求職平台 × 人頭帳戶（2,642 筆）」改過來 ——
那是提案階段的估計，全量語料實測後改為 Threads，理由見 REPORT.md §1。
2026-09-23 與模組 D 談定界線，超商物流讓給 D，語料由 9,167 重切為 4,140 筆。

    m1_corpus.py     S9   切出自己的語料（含與 D 的界線 EXCLUDE_PATTERN）  ✓
    m2_vision.py     S11  Threads 貼文、私訊對話、假認證頁的截圖理解（截圖集已備）
    m3_retrieval.py  S12  BM25 ＋ 語意向量（bge-m3）RRF 融合檢索  ✓
    m4_judgement.py  S13  判讀與歷程抽取，接 threads_stages 的 12 階段  ✓
    m5_agent.py      S14  狀態機與行動劇本
"""
