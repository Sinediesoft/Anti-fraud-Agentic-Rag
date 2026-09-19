"""把 165 打詐儀表板的案例語料抓下來，存成 parquet（S6）。

出處（2026-09-19 由刑事局官網的連結確認）：

    儀表板  https://165dashboard.tw/
    端點    POST /CIB_DWS_API/api/CaseSummary/GetCaseSummaryList
    授權    站上沒有 robots.txt，這是公開的政府儀表板

回傳欄位剛好對得上專案的正規化 schema：

    Id        -> case_id        CaseDate  -> date
    CityName  -> county         CityId    -> county_id
    Summary   -> text           CaseTitle -> label（手法分類）

只用標準函式庫打 HTTP，寫 parquet 用 pyarrow。不必裝 polars 或 httpx。

禮貌：19 萬筆分 10 頁，每頁之間停 2 秒。這是政府的公開服務，沒有理由
把它打爆 —— 而且抓一次就夠，這支不該被排程反覆跑。

⚠ 案例內文是真實受害者的第一人稱敘述。抓下來的檔案不進版控（.gitignore
   已擋 /data/* 與 *.parquet），使用界線見 data/README.md。
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://165dashboard.tw"
ENDPOINT = "/CIB_DWS_API/api/CaseSummary/GetCaseSummaryList"
SOURCE_ID = "165"

# 欄位對映。改這裡就能跟著上游改名，不必翻程式。
FIELD_MAP = {
    "Id": "case_id",
    "Summary": "text",
    "CaseTitle": "label",
    "CaseDate": "date",
    "CityName": "county",
    "CityId": "county_id",
}


def _request_once(page: int, per_page: int, timeout: int) -> bytes:
    body = json.dumps(
        {
            "UsingPaging": True,
            "NumberOfPerPage": per_page,
            "PageIndex": page,
            "SortOrderInfos": [{"SortField": "CaseDate", "SortOrder": "DESC"}],
            "SearchTermInfos": [],
        }
    ).encode("utf-8")

    req = urllib.request.Request(BASE_URL + ENDPOINT, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    # 表明身分，不偽裝成瀏覽器
    req.add_header("User-Agent", "anti-fraud-copilot/0.1 (course project; corpus fetch)")

    with urllib.request.urlopen(req, timeout=timeout) as r:
        declared = r.headers.get("Content-Length")
        raw = r.read()

    # 一頁 2 萬筆大約 31 MB。實測過連線會在中途斷掉，而斷點如果落在
    # \uXXXX 中間，json 會報「Invalid \uXXXX escape」—— 看起來像上游
    # 資料壞掉，其實是讀取不完整。所以收到多少一定要跟宣告的比對。
    if declared is not None and len(raw) != int(declared):
        raise OSError(f"讀取不完整：收到 {len(raw):,} bytes，宣告 {int(declared):,} bytes")
    return raw


def fetch_page(page: int, per_page: int, timeout: int, attempts: int = 4) -> dict:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            payload = json.loads(_request_once(page, per_page, timeout))
        except (OSError, json.JSONDecodeError) as e:
            last = e
            wait = 3 * attempt
            print(
                f"[!]  第 {page} 頁第 {attempt}/{attempts} 次失敗（{type(e).__name__}），{wait} 秒後重試"
            )
            time.sleep(wait)
            continue

        if not payload.get("isSuccess"):
            raise RuntimeError(
                f"第 {page} 頁失敗：code={payload.get('code')} {payload.get('message')}"
            )
        return payload["body"]

    raise RuntimeError(f"第 {page} 頁重試 {attempts} 次仍失敗") from last


def normalise(rows: list[dict]) -> list[dict]:
    out = []
    for raw in rows:
        rec = {dst: raw.get(src) for src, dst in FIELD_MAP.items()}
        rec["case_id"] = str(rec["case_id"] or "")
        rec["source"] = SOURCE_ID  # 多來源時 case_id 會撞號，出處是 source + case_id
        out.append(rec)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default=str(REPO_ROOT / "data" / "cases_165.parquet"))
    ap.add_argument("--per-page", type=int, default=20000)
    ap.add_argument("--delay", type=float, default=2.0, help="每頁之間停幾秒")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--max-pages", type=int, default=0, help="只抓前幾頁（測試用，0＝全部）")
    args = ap.parse_args()

    started = datetime.now(UTC)
    print(f"[i]  來源 {BASE_URL}{ENDPOINT}")
    print(f"[i]  每頁 {args.per_page} 筆，每頁之間停 {args.delay} 秒")

    first = fetch_page(1, args.per_page, args.timeout)
    total = first["RecordCount"]
    pages = first["TotalPages"]
    if args.max_pages:
        pages = min(pages, args.max_pages)
    print(f"[i]  上游宣告 {total:,} 筆、{first['TotalPages']} 頁；本次抓 {pages} 頁")

    records = normalise(first["Detail"])
    print(f"[OK] 第 1/{pages} 頁 -> {len(records):,} 筆")

    for page in range(2, pages + 1):
        time.sleep(args.delay)
        body = fetch_page(page, args.per_page, args.timeout)
        got = normalise(body["Detail"])
        records.extend(got)
        print(f"[OK] 第 {page}/{pages} 頁 -> {len(got):,} 筆（累計 {len(records):,}）")

    seen, unique = set(), []
    for r in records:
        if r["case_id"] and r["case_id"] not in seen:
            seen.add(r["case_id"])
            unique.append(r)
    dupes = len(records) - len(unique)

    import pyarrow as pa
    import pyarrow.parquet as pq

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(unique)
    pq.write_table(table, out, compression="zstd")

    finished = datetime.now(UTC)
    meta = {
        "source": SOURCE_ID,
        "base_url": BASE_URL,
        "endpoint": ENDPOINT,
        "fetched_at": started.isoformat(timespec="seconds"),
        "seconds": round((finished - started).total_seconds(), 1),
        "upstream_record_count": total,
        "rows_written": len(unique),
        "duplicates_dropped": dupes,
        "per_page": args.per_page,
        "columns": list(table.column_names),
    }
    meta_path = out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"[OK] 寫入 {out}（{out.stat().st_size / 1024 / 1024:.1f} MB）")
    print(f"[OK] 出處紀錄 {meta_path}")
    if dupes:
        print(f"[!]  丟掉 {dupes:,} 筆重複或無編號的資料")
    print()
    print("把這三行填進 data/README.md 的 TODO(S6)：")
    print(f"  - 去哪裡拿（base URL）：{BASE_URL}")
    print(f"  - 怎麼拿：POST {ENDPOINT}，每頁 {args.per_page} 筆、共 {pages} 頁")
    print(f"  - 哪天抓的：{started.date()}（{len(unique):,} 筆）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
