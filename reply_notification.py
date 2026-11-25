"""Browser-side, default-on completion chime. No external asset or dependency."""

HTML = """
<button type="button" aria-pressed="true">關閉回覆音效</button>
<div class="hint" role="status" aria-live="polite">預設開啟；送出問題時會嘗試解除瀏覽器音效限制。</div>
"""

CSS = """
button {
    font: inherit;
    font-size: .875rem;
    color: var(--st-text-color);
    background: var(--st-background-color);
    border: 1px solid var(--st-border-color, #888);
    border-radius: var(--st-button-radius, .5rem);
    padding: .45rem .7rem;
    cursor: pointer;
    width: 100%;
}
button:focus-visible, button:hover { border-color: var(--st-primary-color); }
.hint { font-size: .75rem; margin-top: .4rem; opacity: .8; }
"""

JS = """
export default function(component) {
    const { parentElement, data } = component;
    const button = parentElement.querySelector("button");
    const hint = parentElement.querySelector(".hint");
    // 同一頁共用一個 audio context，避免 Streamlit rerun 後丟失已解鎖的音效。
    // 設定與 AudioContext 生命週期分開；整頁重載時恢復預設開啟。
    const key = Symbol.for("event-support-agent.reply-chime.v2");
    const state = window[key] || (window[key] = {
        context: null, enabled: true, seen: null, pending: null, nodes: new Set()
    });
    let active = true;
    const update = (message) => {
        if (!active) return;
        button.textContent = state.enabled ? "關閉回覆音效" : "開啟回覆音效";
        button.setAttribute("aria-pressed", String(state.enabled));
        hint.textContent = message || (!state.enabled
            ? "音效已關閉；可隨時重新開啟。"
            : state.context && state.context.state === "running"
                ? "已開啟；新回覆完成或失敗時提醒。"
                : "預設開啟；送出問題或點擊頁面時嘗試解除瀏覽器音效限制。");
    };
    const stop = () => {
        for (const oscillator of state.nodes) {
            try { oscillator.stop(); } catch (_) {}
        }
        state.nodes.clear();
    };
    const chime = (error) => {
        const ctx = state.context;
        if (!ctx || ctx.state !== "running") return false;
        stop();
        const notes = error ? [523, 392, 330] : [660, 880, 1047];
        const start = ctx.currentTime + 0.02;
        notes.forEach((frequency, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            const t = start + i * 0.24;
            osc.type = "sine";
            osc.frequency.value = frequency;
            gain.gain.setValueAtTime(0.0001, t);
            gain.gain.exponentialRampToValueAtTime(0.25, t + 0.025);
            gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.20);
            osc.connect(gain);
            gain.connect(ctx.destination);
            state.nodes.add(osc);
            osc.onended = () => {
                state.nodes.delete(osc);
                osc.disconnect();
                gain.disconnect();
            };
            osc.start(t);
            osc.stop(t + 0.21);
        });
        return true;
    };
    // 不依賴音效開關本身的點擊；送出問題的正常滑鼠/鍵盤操作
    // 即可嘗試解鎖 Web Audio。瀏覽器拒絕時保持設定開啟，不偷偷關閉。
    const flush = () => {
        if (!active || !state.enabled || !state.pending) return;
        const event = state.pending;
        if (chime(event.error)) {
            state.pending = null;
            update(event.error ? "查詢失敗，已提醒。" : "回覆已完成，已提醒。");
        } else {
            update("瀏覽器尚未允許播放；請點一下頁面或檢查網站音效權限。");
        }
    };
    const unlock = () => {
        if (!active || !state.enabled) return;
        try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!AudioContext) throw new Error("Web Audio unavailable");
            if (!state.context || state.context.state === "closed") {
                state.context = new AudioContext();
            }
            if (state.context.state === "running") {
                flush();
                if (!state.pending) update();
                return;
            }
            // resume 可能等到下一個使用者手勢才完成；不阻塞 UI。
            state.context.resume().then(() => {
                if (!active || !state.enabled) return;
                update();
                flush();
            }).catch(() => {
                update("瀏覽器限制音效；請點一下頁面或檢查網站音效權限。");
            });
        } catch (_) {
            update("無法播放；請確認網站音效權限、分頁靜音及裝置音量。");
        }
    };
    const gesture = (event) => {
        if (event.isTrusted && state.enabled &&
                (!state.context || state.context.state !== "running")) unlock();
    };
    // 捕獲一般操作，包括快速提問、輸入及 Enter；不攔截或讀取按鍵內容。
    for (const name of ["pointerdown", "pointerup", "keydown", "click"]) {
        document.addEventListener(name, gesture, true);
    }
    button.onclick = () => {
        state.enabled = !state.enabled;
        if (!state.enabled) {
            state.pending = null;
            stop();
        } else {
            unlock();
        }
        update();
    };
    update();
    const event = data && data.event;
    if (event && event.id && event.id !== state.seen) {
        state.seen = event.id;
        state.pending = state.enabled ? event : null;
    }
    if (state.enabled && state.pending) {
        // 若未解鎖，保留最新完成事件，等下一次可信手勢播放一次。
        flush();
        if (state.pending) unlock();
    }
    return () => {
        active = false;
        button.onclick = null;
        for (const name of ["pointerdown", "pointerup", "keydown", "click"]) {
            document.removeEventListener(name, gesture, true);
        }
    };
}
"""


def render_reply_notification(st, event):
    """Stable component identity; only completion ID and error flag cross the UI boundary."""
    component = st.components.v2.component(
        "reply_completion_chime", html=HTML, css=CSS, js=JS
    )
    component(key="reply-completion-chime", data={"event": event}, width="stretch")
