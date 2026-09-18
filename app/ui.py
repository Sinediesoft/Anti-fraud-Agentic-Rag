"""介面（說明書 S7 第 5 點）。

三個區塊：輸入區、結果區、執行紀錄區，加一個免費版／解鎖版切換鈕。
執行紀錄不是除錯工具，是展示重點 —— 放在看得見的地方，不要收在摺疊選單裡。

跑法：make run MODULE=a_tbd  ／  make run MODULE=all
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# streamlit run 是把這個檔案當腳本執行，所以 __package__ 是空的、相對匯入會失效。
# 把儲存庫根目錄與 packages/ 放進搜尋路徑，之後一律用絕對匯入。
_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "packages"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import streamlit as st  # noqa: E402
from contracts import AnalyzeInput, CoverageStatus, ImageInput, RiskLevel  # noqa: E402

from app.shell import Shell  # noqa: E402

RISK_STYLE: dict[RiskLevel, tuple[str, str]] = {
    RiskLevel.UNKNOWN: ("⚪", "尚無法判斷"),
    RiskLevel.LOW: ("🟢", "低"),
    RiskLevel.MEDIUM: ("🟡", "中"),
    RiskLevel.HIGH: ("🟠", "高"),
    RiskLevel.CRITICAL: ("🔴", "非常高"),
}


def _selection() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", default="all")
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args.module


def main() -> None:
    st.set_page_config(page_title="防詐 Copilot", page_icon="🛡️", layout="wide")
    selection = _selection()

    st.title("🛡️ 防詐 Copilot")
    st.caption(
        "用你自己的話講，或直接上傳截圖 —— 我們會告訴你這是哪一類詐騙、你走到哪一步了、接下來該做什麼。"
    )

    with st.sidebar:
        st.subheader("方案")
        unlocked = st.toggle("解鎖版", value=False, help="切換看同一筆輸入在免費版與解鎖版的差別")
        st.caption("風險等級與 165 專線永遠免費顯示。")

        shell = Shell.boot(selection, unlocked=unlocked)

        st.subheader("已載入的模組")
        if not shell.registry.loaded:
            st.info("目前沒有模組。`_template` 要用 `make run MODULE=_template` 指名載入。")
        for m in shell.registry.loaded:
            combo = (
                f"{m.pack.platform} × {m.pack.tactic}"
                if m.pack.is_configured
                else "平台 × 手法未設定"
            )
            st.write(f"{'🟢' if m.usable else '🟡'} **{m.pack.name}** — {combo}")
        for f in shell.registry.failures:
            st.error(f"{f.module_id}：{f.reason}")

        manual = st.selectbox(
            "手動指定模組（最後一道保險）",
            options=["自動路由", *[m.id for m in shell.registry.loaded]],
        )

    left, right = st.columns([3, 2])

    with left:
        st.subheader("① 你遇到的事")
        text = st.text_area(
            "用自己的話講就好",
            height=180,
            placeholder="例：群組裡的老師叫我先入金才能出金，我已經匯了三次…",
        )
        uploads = st.file_uploader(
            "截圖（可以不放）", type=["png", "jpg", "jpeg"], accept_multiple_files=True
        )
        go = st.button("看看我遇到什麼", type="primary", use_container_width=True)

    if not go:
        return

    images: list[ImageInput] = []
    for up in uploads or []:
        tmp = Path(tempfile.gettempdir()) / up.name
        tmp.write_bytes(up.getvalue())
        images.append(ImageInput(path=str(tmp), filename=up.name))

    payload = AnalyzeInput(text=text, images=images)
    if payload.is_empty:
        st.warning("請至少打幾個字，或上傳一張截圖。")
        return

    response = shell.analyze(payload, manual=None if manual == "自動路由" else manual)

    with left:
        st.subheader("② 判讀結果")
        icon, label = RISK_STYLE[response.risk_level]
        st.metric("風險等級", f"{icon} {label}")
        st.info(f"☎️ 反詐騙諮詢專線 **{response.hotline}** —— 任何情況都可以直接打。")

        if response.coverage is CoverageStatus.COVERED_LOCKED:
            st.warning(response.locked_notice)
        elif response.coverage is CoverageStatus.UNCOVERED:
            st.warning("你的情況目前還沒有對應的判讀模組，但下面這些事現在就可以做。")

        for advice in response.general_advice:
            st.write(f"- {advice}")

        verdict = response.verdict
        if verdict is not None:
            st.write(f"**詐騙類型**：{verdict.scam_type or '—'}")
            st.write(f"**目前階段**：{verdict.scam_stage or '—'}")
            if verdict.stage_explanation:
                st.write(verdict.stage_explanation)

            st.markdown("#### 接下來該做什麼")
            for action in sorted(verdict.actions, key=lambda a: a.order):
                prefix = "🔔 " if action.preventive else ""
                st.write(f"{action.order}. {prefix}{action.text}")
                if action.why:
                    st.caption(f"　 為什麼：{action.why}")

            if verdict.similar_cases:
                st.markdown("#### 相似案例")
                for case in verdict.similar_cases:
                    meta = " · ".join(x for x in [case.date, case.county, case.label] if x)
                    st.write(f"`{case.case_id}` {meta}")
                    st.caption(case.excerpt)

            if verdict.legal_refs:
                st.markdown("#### 相關法規")
                for ref in verdict.legal_refs:
                    st.write(f"- {ref.title} {ref.article}（版本 {ref.version_date}）")

        for hint in response.hints:
            suffix = "（需要解鎖）" if hint.locked else ""
            st.info(f"你可能同時也遇到「{hint.module_name}」{suffix}")

        st.caption(response.disclaimer)

    with right:
        st.subheader("③ 執行紀錄")
        st.caption("這次判讀呼叫了哪些步驟、各花多久。")
        total = sum(e.duration_ms for e in response.trace)
        st.metric("總耗時", f"{total:.0f} ms")
        for event in response.trace:
            mark = {"ok": "✅", "degraded": "🟡", "failed": "❌", "skipped": "⏭️"}.get(
                event.status, "•"
            )
            st.write(f"{mark} `{event.step}` — {event.duration_ms:.0f} ms")
            if event.detail:
                st.caption(f"　 {event.detail}")

        if response.scores:
            st.markdown("#### 各模組認領分數")
            st.bar_chart(response.scores)


main()
