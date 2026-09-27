"""介面（說明書 S7 第 5 點）。

兩個分頁：

    ① 對話    多輪追問三題，再交給分數最高的那一個模組。app/chat.py 的外皮。
    ② 單次查詢 原本的表單，一句話直接判讀，可以手動指定模組。兩個分頁走的是
              同一條 shell.analyze() —— 差別只在有沒有先追問，展示時兩邊
              對照著看，才說得出對話層到底加了什麼。

執行紀錄不是除錯工具，是展示重點 —— 放在看得見的地方，不要收在摺疊選單裡。

跑法：make run MODULE=a_tbd  ／  make run MODULE=all
"""

from __future__ import annotations

import argparse
import sys
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
from app.uploads import save_upload  # noqa: E402

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
    # 存檔用內容雜湊當檔名 —— 同名的不同截圖才不會互相覆蓋（理由見 app/uploads.py）
    return [save_upload(up.name, up.getvalue()) for up in uploads or []]


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
#  token 用量
# ══════════════════════════════════════════════════════════════
#
# 分成「送出前」與「判讀後」兩段，因為這兩個數字的可信度不一樣，混成一格
# 會騙到自己：
#
#   送出前  這一次會吃掉多少 —— 嵌入模型載入之後是 tokenizer 真的切出來的
#           精確值，載入之前只能估（約 1.27 字/token）。
#   判讀後  實際吃掉了多少 —— Ollama 與 tokenizer 回報的真值，蓋掉上面那個。
#
# 為什麼不是每打一個字就跳：Streamlit 的輸入元件只在送出、失焦或有按鈕觸發
# rerun 時才把值交回 Python，沒有逐鍵事件。真要逐鍵只能用 components.v1.html
# 塞一段 JS，但那段 JS 在自己的 iframe 裡看不到 st.chat_input 的內容 ——
# 等於要把輸入框整個換掉，代價遠大於效益。所以這裡的「即時」是「每一次
# 互動都重算」，而不是「每一個按鍵都重算」。
#
# 沒有雲端（已砍），所以這一格不是在算錢，是在看三件事：512 的截斷預警、
# 8192 上下文的餘裕、以及 tok/s（那個數字要寫進報告）。


def _snapshot_usage(key: str) -> None:
    """把這一次記到的 token 帳存進這個連線自己的狀態。

    只在有東西時才蓋掉舊的：追問那幾輪不呼叫任何模型（路由只用關鍵詞算分），
    如果照樣覆蓋，使用者會看到上一次的真實數字忽然變成空白，像壞掉一樣。
    """
    if usage := models.usage_log():
        st.session_state[key] = usage


def _live_tokens(text: str) -> tuple[int, bool]:
    """（token 數, 是不是精確值）。精確值要等嵌入模型的 tokenizer 載入。"""
    exact = models.count_embed_tokens(text)
    if exact is not None:
        return exact, True
    return models.estimate_embed_tokens(text), False


def _render_live_tokens(text: str, *, images: int = 0) -> None:
    """送出前：這段文字進檢索會吃掉多少。"""
    limit = models.EMBED_MAX_TOKENS
    n, exact = _live_tokens(text)
    # 單位放標籤不放數值：右欄只有畫面寬度的五分之二，「528 / 512 token」
    # 在筆電的視窗寬度下會被 st.metric 截成「528 / 512 to…」。
    st.metric("檢索 token", f"{n:,} / {limit:,}")
    st.progress(min(n / limit, 1.0) if limit else 0.0)
    st.caption(
        "這次要送進檢索的文字，tokenizer 真的切出來的長度。"
        if exact
        else "這次要送進檢索的文字，估的（約 1.27 字/token）—— 嵌入模型還在背景載入，載完換精確值。"
    )
    if n > limit:
        st.warning(
            f"超過 {limit:,} token —— bge-m3 只吃前面那 {limit:,} 個，後面會被無聲截掉。"
            "講重點，或分兩次問。"
        )
    if images:
        st.caption(f"另外有 {images} 張截圖：OCR 出來的文字要等判讀時才會加進去，實際會比這裡多。")


def _render_usage(usage: list[models.Usage]) -> None:
    """判讀後：真的吃掉了多少。"""
    if not usage:
        # 兩個分頁共用這一句，所以講的是「路由」不是「追問」—— 單次查詢沒有追問。
        st.caption("還沒有呼叫過模型 —— 路由只用關鍵詞算分，一個 token 都不花。")
        return

    embed = [u for u in usage if u.purpose == "embedding"]
    slm = [u for u in usage if u.purpose == "slm"]
    st.caption("最後一次真的呼叫模型的那一次：")

    if embed:
        total = sum(u.prompt_tokens for u in embed)
        items = sum(u.items for u in embed)
        longest = max(u.longest for u in embed)
        dropped = sum(u.dropped for u in embed)
        st.write(f"🔎 **檢索** `{embed[0].model}` —— {total:,} token／{items} 段，最長 {longest:,}")
        if dropped:
            st.error(
                f"最長那一段超過 {embed[0].limit:,} token，被無聲截掉 {dropped:,} 個 —— "
                f"檢索只看得到前面那 {embed[0].limit:,} 個。"
            )

    if not slm:
        st.caption("🧠 生成：這次沒有呼叫地端模型（只有接上生成的模組才有這一段）。")
        return

    u = slm[-1]
    st.write(
        f"🧠 **生成** `{u.model}` —— 送進 {u.prompt_tokens:,} ＋ 吐出 {u.output_tokens:,} token"
    )
    st.caption(f"佔 num_ctx {u.limit:,} 的 {u.load_pct:.1f}%　·　{u.tokens_per_s:.0f} tok/s")
    if u.estimated_tokens:
        gap = u.prompt_tokens - u.estimated_tokens
        st.caption(
            f"送出前估 {u.estimated_tokens:,}，實際 {u.prompt_tokens:,} token（差 {gap:+,}）"
        )
    if u.truncated:
        st.error(
            "prompt 超過 num_ctx —— Ollama 不是切掉超出的部分，而是從前面砍到只剩上限的"
            "一半（2026-09-27 實測），最前面的系統指示可能整段不見了。"
        )


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
            with st.spinner("交給最有把握的那個模組判讀…"):
                models.reset_usage()
                chat.finish()
            _snapshot_usage("chat_usage")
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
            models.reset_usage()
            chat.send(prompt)
        _snapshot_usage("chat_usage")
        st.rerun()

    with right:
        st.subheader("token 用量")
        _render_live_tokens(chat.text, images=len(chat.images))
        _render_usage(st.session_state.get("chat_usage", []))

        st.subheader("執行紀錄")
        if chat.score_history:
            st.markdown("#### 每問一題，分數怎麼變")
            st.caption("對話層的價值就在這張圖：第一句話幾乎不可能讓任何模組認領。")
            st.line_chart(chat.score_history)
        if chat.response is not None:
            st.markdown("#### 最後送去判讀的那一次")
            _render_trace(chat.response.trace, chat.response.scores, key="chat")


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

    with right:
        st.subheader("token 用量")
        _render_live_tokens(text, images=len(uploads or []))
        # 真值要等判讀跑完才有。這個分頁不像對話分頁那樣會 st.rerun()，
        # 所以先在右欄留一個位子，判讀完再把真值填進同一個位子。
        usage_slot = st.empty()
    with usage_slot.container():
        _render_usage(st.session_state.get("form_usage", []))

    if not go:
        return

    payload = AnalyzeInput(text=text, images=_uploads_to_images(uploads))
    if payload.is_empty:
        st.warning("請至少打幾個字，或上傳一張截圖。")
        return

    models.reset_usage()
    response = shell.analyze(payload, manual=manual)
    _snapshot_usage("form_usage")
    with usage_slot.container():
        _render_usage(st.session_state.get("form_usage", []))

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
