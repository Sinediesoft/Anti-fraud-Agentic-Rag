"""介面（說明書 S7 第 5 點）。

兩個分頁：

    ① 對話    多輪追問三題，再把所有認領的模組都喚起。app/chat.py 的外皮。
    ② 單次查詢 原本的表單。留著是因為 evaluate.py 的 20 題與 S16 入場檢查
              走的是 shell.analyze() 那條路 —— 砍掉等於自斷考核，而且展示時
              兩邊對照著看，才說得出對話層到底加了什麼。

執行紀錄不是除錯工具，是展示重點 —— 放在看得見的地方，不要收在摺疊選單裡。

跑法：make run MODULE=a_tbd  ／  make run MODULE=all
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

# streamlit run 是把這個檔案當腳本執行，所以 __package__ 是空的、相對匯入會失效。
# 把儲存庫根目錄與 packages/ 放進搜尋路徑，之後一律用絕對匯入。
_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "packages"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import streamlit as st  # noqa: E402
from contracts import AnalyzeInput, CoverageStatus, ImageInput, RiskLevel  # noqa: E402
from shared import models  # noqa: E402

from app.chat import ChatSession, Phase  # noqa: E402
from app.shell import Shell  # noqa: E402

RISK_STYLE: dict[RiskLevel, tuple[str, str]] = {
    RiskLevel.UNKNOWN: ("⚪", "尚無法判斷"),
    RiskLevel.LOW: ("🟢", "低"),
    RiskLevel.MEDIUM: ("🟡", "中"),
    RiskLevel.HIGH: ("🟠", "高"),
    RiskLevel.CRITICAL: ("🔴", "非常高"),
}


# 執行紀錄是攤平的巢狀結構（見 app/shell.py）：
#
#     route:<模組>…  →  analyze:<模組>  →  模組自己的子步驟…  →  guards
#                        └ 父層，整個 analyze() 的牆鐘時間 ┘
#
# 全部相加等於把模組那段算兩次。2026-09-21 實測：父層 7141 ms 被加成 14281 ms，
# 畫面上的「總耗時」剛好是實際的兩倍。所以只加外殼自己那幾筆 —— 父層已經
# 涵蓋所有子步驟了。這幾個名字全部由 app/shell.py 產生，不是模組取的。
SHELL_STEPS = ("route:", "entitlement", "analyze:", "guards")


@st.cache_resource(show_spinner=False)
def _warm_up_embedder() -> bool:
    """開機時就把嵌入模型載進記憶體，不要等使用者按下按鈕才開始載。

    bge-m3 是 2.2GB。2026-09-21 實測第一次 embed() 要 3.5 秒（機器閒著）到
    6.7 秒（有背景工作在搶 CPU），而那段時間現在全部算在第一次查詢的
    「m3:檢索相似案例」上 —— 檢索本身其實不到 15 ms。

    丟到背景執行緒而不是同步載：畫面先畫出來，模型在使用者打字的時候載完。
    同步載只是把等待從查詢搬到開機，沒有省到任何人的時間。

    失敗不處理：沒裝 ml 那組套件的人本來就會落到檢索的第三層退路（字元重疊），
    預熱失敗不該讓整個畫面爆掉。st.cache_resource 保證每個 process 只跑一次。
    """

    def _load() -> None:
        try:
            models.embed(["暖機"])
        except Exception:
            pass

    threading.Thread(target=_load, daemon=True, name="embed-warmup").start()
    return True


def _selection() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", default="all")
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args.module


def _uploads_to_images(uploads) -> list[ImageInput]:
    images: list[ImageInput] = []
    for up in uploads or []:
        tmp = Path(tempfile.gettempdir()) / up.name
        tmp.write_bytes(up.getvalue())
        images.append(ImageInput(path=str(tmp), filename=up.name))
    return images


def _render_trace(trace, scores, *, key: str) -> None:
    st.caption("這次判讀呼叫了哪些步驟、各花多久。")
    total = sum(e.duration_ms for e in trace if e.step.startswith(SHELL_STEPS))
    st.metric("總耗時", f"{total:.0f} ms")
    for event in trace:
        mark = {"ok": "✅", "degraded": "🟡", "failed": "❌", "skipped": "⏭️"}.get(event.status, "•")
        st.write(f"{mark} `{event.step}` — {event.duration_ms:.0f} ms")
        if event.detail:
            st.caption(f"　 {event.detail}")
    if scores:
        st.markdown("#### 各模組認領分數")
        st.bar_chart(scores)


# ══════════════════════════════════════════════════════════════
#  ① 對話
# ══════════════════════════════════════════════════════════════


def _chat_session(shell: Shell) -> ChatSession:
    session = st.session_state.get("chat")
    if session is None:
        session = ChatSession(shell=shell)
        st.session_state["chat"] = session
        session.greet()
    else:
        # 側邊欄每次 rerun 都會重建 Shell。換上新的，方案切換才會立刻生效。
        session.shell = shell
    return session


def _chat_tab(shell: Shell) -> None:
    chat = _chat_session(shell)
    left, right = st.columns([3, 2])

    with left:
        for message in chat.history:
            with st.chat_message("user" if message.role == "user" else "assistant"):
                st.markdown(message.text)

        if chat.phase is Phase.COLLECTING:
            left_n = chat.questions_left
            st.caption(
                f"還會問你 {left_n} 個問題，問完就給完整判讀。"
                if left_n
                else "問完了 —— 再回一句，就給你完整判讀。"
            )
        controls = st.columns([1, 1, 2])
        if controls[0].button(
            "直接看結果",
            disabled=chat.phase is Phase.DONE or not chat.utterances,
            help="不想再回答了也沒關係，用目前講的內容就判讀",
        ):
            with st.spinner("讓所有相關的模組都跑一遍…"):
                chat.finish()
            st.rerun()
        if controls[1].button("重新開始"):
            chat.restart()
            chat.greet()
            st.rerun()

        uploads = st.file_uploader(
            "截圖（可以不放）",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="chat_uploads",
        )

    if prompt := st.chat_input("用你自己的話講就好…"):
        chat.add_images(_uploads_to_images(uploads))
        with st.spinner("想一下…"):
            chat.send(prompt)
        st.rerun()

    with right:
        st.subheader("執行紀錄")
        if chat.score_history:
            st.markdown("#### 每問一題，分數怎麼變")
            st.caption("對話層的價值就在這張圖：第一句話幾乎不可能讓任何模組認領。")
            st.line_chart(chat.score_history)
        if chat.responses:
            # 數的是被喚起的模組，不是出得了判讀的 —— 未解鎖那個也跑過解鎖層，
            # 它的 trace 一樣要看得到（那正是規則一在畫面上的證據）。
            awakened = [r for r in chat.responses if r.coverage is not CoverageStatus.UNCOVERED]
            st.markdown(f"#### 喚起了 {len(awakened)} 個模組")
            for response in chat.responses:
                _render_trace(response.trace, response.scores, key=f"chat-{id(response)}")


# ══════════════════════════════════════════════════════════════
#  ② 單次查詢（原本的表單）
# ══════════════════════════════════════════════════════════════


def _form_tab(shell: Shell, manual: str | None) -> None:
    left, right = st.columns([3, 2])

    with left:
        st.subheader("① 你遇到的事")
        text = st.text_area(
            "用自己的話講就好",
            height=180,
            placeholder="例：群組裡的老師叫我先入金才能出金，我已經匯了三次…",
            key="form_text",
        )
        uploads = st.file_uploader(
            "截圖（可以不放）",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="form_uploads",
        )
        go = st.button("看看我遇到什麼", type="primary", use_container_width=True)

    if not go:
        return

    payload = AnalyzeInput(text=text, images=_uploads_to_images(uploads))
    if payload.is_empty:
        st.warning("請至少打幾個字，或上傳一張截圖。")
        return

    response = shell.analyze(payload, manual=manual)

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
        _render_trace(response.trace, response.scores, key="form")


# ══════════════════════════════════════════════════════════════


def main() -> None:
    st.set_page_config(page_title="防詐 Copilot", page_icon="🛡️", layout="wide")
    _warm_up_embedder()
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
        st.caption("手動指定只影響「單次查詢」分頁。")

    對話, 單次查詢 = st.tabs(["💬 對話", "📋 單次查詢"])
    with 對話:
        _chat_tab(shell)
    with 單次查詢:
        _form_tab(shell, None if manual == "自動路由" else manual)


main()
