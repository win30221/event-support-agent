"""Browser-side, opt-in completion chime. No external asset or dependency."""

HTML = """
<button type="button" aria-pressed="false">啟用回覆音效並試聽</button>
<div class="hint" role="status" aria-live="polite">啟用後，新回覆完成或失敗時會播放提示音。</div>
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
    // 不存入 localStorage；整頁重載後須重新啟用。
    const key = Symbol.for("event-support-agent.reply-chime.v1");
    const state = window[key] || (window[key] = {
        context: null, enabled: false, seen: null, nodes: new Set()
    });
    let active = true;
    const update = (message) => {
        if (!active) return;
        button.textContent = state.enabled ? "關閉回覆音效" : "啟用回覆音效並試聽";
        button.setAttribute("aria-pressed", String(state.enabled));
        hint.textContent = message || (state.enabled
            ? "已啟用；新回覆完成或失敗時提醒。"
            : "點一下啟用並試聽；重新整理頁面後需再啟用。");
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
    button.onclick = async () => {
        if (state.enabled) {
            state.enabled = false;
            stop();
            update();
            return;
        }
        try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!AudioContext) throw new Error("Web Audio unavailable");
            if (!state.context || state.context.state === "closed") {
                state.context = new AudioContext();
            }
            await state.context.resume();
            if (!active) return;
            state.enabled = state.context.state === "running";
            if (!state.enabled || !chime(false)) throw new Error("Audio blocked");
            update("已啟用（剛才為試聽音）；可切換其他分頁等待。");
        } catch (_) {
            state.enabled = false;
            update("無法播放；請確認網站音效權限、分頁靜音及裝置音量後重試。");
        }
    };
    update();
    const event = data && data.event;
    if (event && event.id && event.id !== state.seen) {
        state.seen = event.id;
        if (state.enabled) {
            if (chime(event.error)) {
                update(event.error ? "查詢失敗，已提醒。" : "回覆已完成，已提醒。");
            } else {
                state.enabled = false;
                update("瀏覽器暫停音效；請再次點擊啟用。");
            }
        }
    }
    return () => {
        active = false;
        button.onclick = null;
    };
}
"""


def render_reply_notification(st, event):
    """Stable component identity; only completion ID and error flag cross the UI boundary."""
    component = st.components.v2.component(
        "reply_completion_chime", html=HTML, css=CSS, js=JS
    )
    component(key="reply-completion-chime", data={"event": event}, width="stretch")
