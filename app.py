import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import streamlit as st

from agent import answer
from guide import CATALOG
from starter_questions import render_starter_questions
from reply_notification import render_reply_notification


TOOL_LABELS = {
    "find_rules": "活動公告",
    "list_shop_items": "活動商城清單",
    "find_shop_item": "商城商品核對",
    "get_my_event_progress": "目前帳號的活動進度",
}


@st.cache_resource
def workers():
    return ThreadPoolExecutor(max_workers=6)


@st.cache_data
def practice_cases():
    path = Path(__file__).resolve().parent / "evaluations" / "rag_cases.json"
    return {case["label"]: case for case in json.loads(path.read_text(encoding="utf-8"))}


def timestamp():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


# 背景執行緒執行一次 Agent 請求；回傳正文、參考資料及獨立工程紀錄。
def run_query(player, provider, history, question):
    interactions = []
    references = []

    def observe(title, data):
        interactions.append({"title": title, "time": timestamp(), "data": data})
        if title == "Python 檢查回答依據":
            references[:] = data.get("參考資料", [])

    try:
        reply, trace = asyncio.run(answer(player, provider, history, question, observe=observe))
        observe("送回聊天頁面", {"回答": reply, "參考資料": references})
        turn = {"role": "assistant", "content": reply, "trace": trace,
                "references": references}
    except Exception as error:
        while isinstance(error, BaseExceptionGroup):
            error = error.exceptions[0]
        turn = {"role": "error", "content": f"查詢失敗：{error}"}
        observe("查詢失敗", {"訊息": str(error)})
    turn["interactions"] = interactions
    return turn


def show_interactions(turn, component_key):
    """Copy the current turn's complete log without rendering it in the chat."""
    events = turn.get("interactions")
    if events:
        copy_interaction_log(
            key=component_key,
            data={"text": json.dumps(events, ensure_ascii=False, indent=2, default=str)},
            width="content",
        )


def show_references(turn):
    """Player-facing references, independent of engineering mode."""
    references = turn.get("references") or []
    if references:
        with st.expander(f"參考資料（{len(references)}）", expanded=False):
            for reference in references:
                label = reference["來源"]
                if reference.get("發布日"):
                    label += f" · {reference['發布日']}"
                st.caption(label)


def show_tool_summary(turn):
    """Describe actual tool calls without claiming every retrieved item was cited."""
    trace = turn.get("trace")
    if trace is None:
        st.caption("這則回覆沒有可顯示的工具紀錄。")
    elif not trace:
        st.caption("這則回覆不需要呼叫活動工具。")
    else:
        with st.expander(f"查詢紀錄 · {len(trace)} 次工具呼叫"):
            for index, call in enumerate(trace, start=1):
                name = call.get("工具", "未知工具")
                args = call.get("參數") or {}
                st.markdown(f"{index}. **{TOOL_LABELS.get(name, name)}**")
                if args.get("edition"):
                    st.caption(f"查詢檔期：{args['edition']}")
            st.caption("這裡顯示本輪查過的資料；最終採用的來源請看參考資料及引用驗證紀錄。")


st.set_page_config(
    page_title=f"{CATALOG.name}｜AI 活動客服 Demo",
    page_icon="🌌",
    layout="centered",
)


copy_interaction_log = st.components.v2.component(
    "copy_interaction_log",
    html='<button type="button" aria-live="polite">複製互動流程</button>',
    css="""
        button {
            font: inherit;
            font-size: .875rem;
            color: var(--st-text-color);
            background: var(--st-background-color);
            border: 1px solid var(--st-border-color, #888);
            border-radius: var(--st-button-radius, .5rem);
            padding: .35rem .75rem;
            cursor: pointer;
        }
        button:hover, button:focus-visible {
            color: var(--st-primary-color);
            border-color: var(--st-primary-color);
        }
        button:disabled { opacity: .7; cursor: default; }
    """,
    js="""
        export default function(component) {
            const { parentElement, data } = component;
            const button = parentElement.querySelector("button");
            let timer;
            let active = true;
            button.disabled = false;
            button.textContent = "複製互動流程";

            button.onclick = async () => {
                clearTimeout(timer);
                button.disabled = true;
                try {
                    await navigator.clipboard.writeText(data.text);
                    if (!active) return;
                    button.textContent = "已複製";
                } catch (error) {
                    if (!active) return;
                    button.textContent = "複製失敗，請重試";
                }
                button.disabled = false;
                timer = setTimeout(() => {
                    button.textContent = "複製互動流程";
                }, 1800);
            };

            return () => {
                active = false;
                clearTimeout(timer);
                button.onclick = null;
            };
        }
    """,
)

st.title(f"{CATALOG.name}｜AI 活動客服")
st.caption("AI Agent Demo · RAG 公告檢索 · Function Calling 工具查詢 · 模擬玩家進度")

with st.container(border=True):
    st.markdown("**第一次體驗？**")
    st.write("這是一個遊戲活動客服示範。你可以詢問活動規則、商城商品，或目前體驗帳號的活動進度。")
    st.caption("所有公告與玩家資料均為模擬資料")

st.sidebar.subheader("體驗設定")
player = st.sidebar.selectbox(
    "模擬玩家",
    ["A", "B", "C"],
    help="切換體驗帳號，可以觀察同一問題如何根據不同玩家進度回答。",
)
provider = st.sidebar.radio(
    "模型服務",
    ["ollama", "openai"],
    format_func=lambda value: {"ollama": "Ollama（本機）", "openai": "OpenAI（雲端）"}[value],
)
developer_mode = st.sidebar.toggle(
    "顯示工程檢視",
    value=True,
    help="顯示查詢紀錄、完整互動流程與複製按鈕。",
)
if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
    st.sidebar.caption("使用 OpenAI 前，需要先設定 OPENAI_API_KEY。")

# 每個體驗帳號與模型服務有獨立對話及待完成請求，避免切換時混入他人資料。
key = f"chat-{player}-{provider}"
turns = st.session_state.setdefault(key, [])
jobs = st.session_state.setdefault("pending_queries", {})
cases = practice_cases()
selected_question = None

if st.sidebar.button("清除目前對話", use_container_width=True):
    old_job = jobs.pop(key, None)
    if old_job is not None:
        old_job.cancel()
    turns.clear()

job = jobs.get(key)
if job is not None and job.done():
    try:
        result = job.result()
    except Exception as error:
        result = {"role": "error", "content": f"查詢失敗：{error}"}
    if isinstance(result, tuple):  # A request started before the app update.
        reply, trace = result
        result = {"role": "assistant", "content": reply, "trace": trace}
    turns.append(result)
    del jobs[key]
    # 只在新結果被收進聊天時產生事件，重繪歷史不會重播。
    st.session_state["reply_notification_event"] = {
        "id": uuid4().hex, "error": result["role"] == "error"
    }

with st.sidebar:
    render_reply_notification(st, st.session_state.get("reply_notification_event"))

if not turns and key not in jobs:
    st.markdown("#### 先試試這些問題")
    selected_question = render_starter_questions(st, cases, player, key)

def show_engineering_details(turn, copy_key):
    """Render diagnostic controls only when engineering mode is enabled."""
    show_tool_summary(turn)
    events = turn.get("interactions")
    if events:
        with st.expander(f"互動流程 · {len(events)} 個步驟"):
            show_interactions(turn, copy_key)
            st.json(events, expanded=False)
    elif turn.get("trace"):
        with st.expander("工具紀錄（舊版對話）"):
            st.json(turn["trace"], expanded=False)


for turn_index, turn in enumerate(turns):
    copy_key = f"copy-log-{key}-{turn_index}"
    if turn["role"] == "error":
        st.error(turn["content"])
        if developer_mode:
            show_engineering_details(turn, copy_key)
        continue
    with st.chat_message(turn["role"]):
        st.write(turn["content"])
        if turn["role"] == "assistant":
            show_references(turn)
            if developer_mode:
                show_engineering_details(turn, copy_key)


@st.fragment(run_every=1)
def show_pending():
    active_job = jobs.get(key)
    if active_job is None or active_job.done():
        st.rerun()
    with st.chat_message("assistant"):
        st.status("正在查詢活動資料並整理回答…", expanded=False, state="running")


if key in jobs:
    show_pending()

typed_question = st.chat_input(
    f"問問看：{CATALOG.name}怎麼玩？",
    key=f"input-{key}",
    disabled=key in jobs,
)
# 快速提問與手動輸入走同一個處理入口，送出原始文字而非評測題目。
question = selected_question or typed_question
if question and key not in jobs:
    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        st.error("請先在啟動服務的環境設定 OPENAI_API_KEY。")
        st.stop()
    history = [turn for turn in turns if turn["role"] != "error"][-8:]
    turns.append({"role": "user", "content": question})
    jobs[key] = workers().submit(run_query, player, provider, history, question)
    st.rerun()
