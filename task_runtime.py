"""Intent-first LangGraph workflow. Only the host commits states and tool calls."""
from __future__ import annotations

from langchain_core.messages import HumanMessage
from intent_analysis import (INTENT_PROMPT as SIMPLE_PROMPT, INTENT_SCHEMA as SIMPLE_SCHEMA,
                             AUDIT_PROMPT, AUDIT_SCHEMA, VERIFY_PROMPT as ANSWER_VERIFY_PROMPT,
                             VERIFY_SCHEMA, SimpleIntentPlan, IntentAudit, AnswerVerification,
                             anchor_intents, bind_intents)
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from pydantic import ValidationError

from agent_runtime import SupportState, TurnRuntime
from answering import (ANSWER_VERIFICATION_FAILED, answer_references, validate_part)
from backend import compact_json
from evidence import model_sources, source_alias, source_map
from model_client import TransientModelError, model_step
from task_business import (inspect_slots, model_task_states,
                           overview_clauses, permits_tool, scoped_evidence,
                           matching_item, terminal_message)
from task_contracts import (
    ASSESSMENT_PROMPT, ASSESSMENT_SCHEMA, DRAFT_PROMPT, DRAFT_SCHEMA,
    DraftBundle, EvidenceAssessment, TERMINAL, record,
)
from tool_selection import MAX_TOOL_CALLS, MAX_TOOL_ROUNDS


class IntentState(SupportState, total=False):
    tasks: list
    task_states: dict
    feedback: dict
    coverage: str
    replanned: bool
    verification_repair: bool


def indexed(items, expected):
    """Every requested task has exactly one result; IDs are assigned by the host."""
    by_id = {item.task_id: item for item in items}
    if len(by_id) != len(items) or set(by_id) != set(expected):
        raise ValueError(f"需求 ID 不符：預期 {sorted(expected)}，收到 {sorted(by_id)}；每個 ID 恰好一項。")
    return by_id


def canonical_ids(short_ids, evidence):
    aliases = source_map(evidence)
    if not short_ids or any(i not in aliases for i in short_ids):
        raise ValueError("來源不屬於該需求的本輪可信資料。")
    return list(dict.fromkeys(aliases[i] for i in short_ids))


def host_part(task, answer, ids, evidence):
    return {"question": task["goal"], "status": "supported", "answer": answer,
            "citations": [{"id": i, "quote": evidence[i]["原文"]} for i in ids]}


class IntentRuntime(TurnRuntime):
    """Reuse registry, native ToolNode, trace and transport; replace global action/missing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.observe("Python Intent Workflow", {"模式": "intent-task-simple", "語意驗證": "on",
                     "說明": "LLM 提案；主機維護需求狀態、必要欄位及停止條件。"})

    def initial_state(self):
        return {**super().initial_state(), "tasks": [], "task_states": {}, "feedback": {},
                "coverage": "unknown", "replanned": False, "verification_repair": False}

    def scope(self, task, state):
        return scoped_evidence(task, state["sources"], state["evidence"])

    def slots(self, task, state):
        return inspect_slots(task, state["sources"], state["evidence"])

    def active(self, state):
        return [t for t in state["tasks"] if state["task_states"][t["id"]]["status"]
                in ("pending", "collecting")]

    def can_query(self, state):
        return (state["round_number"] < MAX_TOOL_ROUNDS and state["attempts"] < MAX_TOOL_CALLS
                and (state["round_number"] <= 1 or state["progress"]))

    def packet(self, state):
        # Private records are exposed only if a current task needs the current account.
        visible = [s for s in state["sources"] if s.get("工具") != "get_my_event_progress"
                   or any(t["account"] == "self" and s.get("檔期") in t["editions"]
                          for t in state["tasks"])]
        return {"玩家原問": self.question, "服務上下文": self.context.public_summary(),
                "需求": model_task_states(state["tasks"], state["task_states"]),
                "工具結果": model_sources(visible, aliases=True),
                "工具錯誤": state["errors"],
                "允許補查": self.can_query(state),
                "可用資料工具": [{"name": d["function"]["name"], "description": d["function"]["description"]}
                               for d in self.definitions],
                "回饋": state["feedback"]}

    async def ask(self, prompt, packet, schema, cls, stage, check=None):
        # A bounded format repair is distinct from business retrieval/semantic repair.
        messages = [{"role": "system", "content": prompt}, *self.conversation[:-1],
                    {"role": "user", "content": compact_json(packet)}]
        for attempt in range(2):
            reply = await model_step(self.provider, messages, [], self.complete, self.observe,
                                     stage if not attempt else stage + " · 契約修正", response_schema=schema)
            try:
                result = cls.model_validate_json(reply.get("content"))
                return check(result) if check else result
            except (ValueError, TypeError, ValidationError) as error:
                self.observe("Python 任務契約驗證失敗", {"階段": stage, "錯誤": str(error)[:600]})
                messages[-1] = {"role": "user", "content": compact_json(
                    {**packet, "契約修正": "依實際錯誤修正資料，不新增未驗證事實。",
                     "錯誤": str(error)[:600]})}
        return None

    def intent_packet(self, feedback):
        # 第一層只理解語意；工具能力與授權限制留給第二層。
        return {"玩家原問": self.question, "服務上下文": self.context.public_summary(),
                "修正回饋": feedback}

    async def classify_intents(self, packet):
        # 正式工作流及獨立評測共用同一份 Prompt、Schema、輸入及原問錨定。
        intents = await self.ask(SIMPLE_PROMPT, packet, SIMPLE_SCHEMA, SimpleIntentPlan,
                                 "精簡意圖分析", lambda p: anchor_intents(p, self.question))
        if intents is not None:
            self.observe("Python 原問需求錨定", {"需求": intents})
        return intents

    async def intent(self, state):
        self.node(state, "intent")
        packet = self.intent_packet(state["feedback"])
        # 語意規劃最多一次重提案；結構錯誤由 ask 各自有限修復。
        # 未通過 precision/recall 審查前不得執行任何資料工具。
        for attempt in range(2):
            intents = await self.classify_intents(packet)
            if intents is None:
                break
            from intent_analysis import CAPABILITIES
            audit_packet = {"玩家原問": packet["玩家原問"], "服務上下文": packet["服務上下文"],
                "能力": "公開公告、公開商城、本人活動紀錄及運算；無寫入或其他帳號工具。",
                "修正回饋": packet["修正回饋"], "需求": intents,
                "允許業務能力": {k: sorted(v) for k, v in CAPABILITIES.items()}}
            def checked(audit):
                return audit, bind_intents(audit, intents, self.context, self.observe)
            reviewed = await self.ask(AUDIT_PROMPT, audit_packet, AUDIT_SCHEMA, IntentAudit,
                                      "意圖與業務範圍驗證", checked)
            if reviewed is None:
                break
            audit, plan = reviewed
            self.observe("Python 意圖驗證", {"判定": audit.decision, "原因": audit.issues})
            if plan is None:
                packet["修正回饋"] = {"意圖缺失": audit.issues}
                continue
            tasks, records = plan
            self.observe("Python 需求清單", {"需求": tasks, "狀態": records})
            next_step = "verify" if not any(r["status"] not in TERMINAL for r in records.values()) else (
                "select" if self.can_query(state) else "assess")
            return {"tasks": tasks, "task_states": records, "coverage": "complete", "next_step": next_step}
        return {"tasks": [], "task_states": {}, "coverage": "failed", "next_step": "finalize"}

    async def prepare_tools(self, state: IntentState):
        # LangGraph uses the node annotation as its input schema. The inherited
        # SupportState annotation otherwise hides tasks/task_states from validate_call.
        return await super().prepare_tools(state)

    def route_next(self, state):
        return state["next_step"]

    async def select(self, state):
        # All tools retain one provider-neutral definition; no query keyword router.
        active = self.active(state)
        hints = []
        for task in active:
            r = state["task_states"][task["id"]]
            slot = self.slots(task, state)
            hints.append({"任務": task, "必要資料": slot["needs"] or r["needs"],
                          "說明": r["reason"]})
        message = HumanMessage(content=compact_json(
            {"主機待處理需求": hints, "說明": "僅查仍有需要的資料；已完成或不支援需求不得執行。"
             "指定商品先查存在性；不要從歷史回答取數值；工具結果足夠時停止。",
             "回饋": state["feedback"]}))
        updates = await super().select({**state, "messages": [*state["messages"], message]})
        updates["messages"] = [message, *updates["messages"]]
        return updates

    def validate_call(self, state, name, raw):
        args = super().validate_call(state, name, raw)
        for task in self.active(state):
            if not permits_tool(task, name, args, state["sources"]):
                continue
            if name == "calculate" and not set(args["source_ids"]) <= set(self.scope(task, state)):
                continue
            if name == "find_shop_item" and task["product"]:
                from guide import normalize_item_name, shop_item
                if normalize_item_name(args["name"]) != normalize_item_name(task["product"]):
                    lookup = shop_item(task["product"], args["edition"])
                    if lookup["status"] != "found" or not matching_item(args["name"], lookup["items"][0]):
                        continue
            needs = state["task_states"][task["id"]]["needs"]
            if needs and not any(n["tool"] == name and n["edition"] == args.get("edition", "") for n in needs):
                continue
            return args
        raise ValueError("工具或參數不屬於目前待處理需求的資料範圍。")

    async def collect(self, state):
        updates = await super().collect(state)
        # Successful duplicate searches are not new evidence. No result is not a reason to regenerate.
        updates["progress"] = bool(set(updates["evidence"]) - set(state["evidence"]))
        self.observe("Python 新證據檢查", {"新增來源": sorted(set(updates["evidence"]) - set(state["evidence"])),
                     "判定": "progress" if updates["progress"] else "no_progress"})
        return updates

    def valid_needs(self, task, proposal, state):
        result = []
        for n in proposal:
            if not n.detail.strip() or len(n.detail) > 1000:
                continue
            if n.tool == "calculate":
                if n.edition or not permits_tool(task, n.tool, {}, state["sources"]):
                    continue
                edition = ""
            else:
                try:
                    edition = self.context.resolve_edition(n.edition)
                except ValueError:
                    continue
                if not permits_tool(task, n.tool, {"edition": edition}, state["sources"]):
                    continue
            entry = {"tool": n.tool, "edition": edition, "detail": n.detail.strip()}
            if entry not in result:
                result.append(entry)
        return result[:5]

    async def assess(self, state):
        self.node(state, "evidence_assessment")
        records = dict(state["task_states"])
        active = self.active(state)
        slots = {t["id"]: self.slots(t, state) for t in active}
        pending = []
        for task in active:
            tid = task["id"]
            slot = slots[tid]
            if slot["kind"] == "blocked":
                records[tid] = record(slot["status"], reason=slot["reason"])
            else:
                pending.append(task)
        decisions = {}
        if pending:
            packet = self.packet({**state, "task_states": records})
            packet["待評估需求"] = [t["id"] for t in pending]
            packet["必要資料檢查"] = {t["id"]: {**slots[t["id"]],
                "ids": [source_alias(i) for i in slots[t["id"]]["ids"]],
                "required": [source_alias(i) for i in slots[t["id"]]["required"]]} for t in pending}
            decisions = await self.ask(ASSESSMENT_PROMPT, packet, ASSESSMENT_SCHEMA, EvidenceAssessment,
                                       "證據充足性評估", lambda p: indexed(p.decisions, [t["id"] for t in pending])) or {}
        for task in pending:
            tid = task["id"]
            slot = slots[tid]
            decision = decisions.get(tid)
            scope = self.scope(task, state)
            if slot["kind"] == "missing":
                # Required data cannot be waived by ready. An explicit request for
                # clarification or evidence that the question is unanswerable must
                # not be turned into an unrelated tool loop.
                if decision is not None and decision.status in ("clarify", "no_evidence", "unavailable") and not decision.needs:
                    records[tid] = record(decision.status, reason=decision.reason)
                else:
                    records[tid] = (record("collecting", needs=slot["needs"], reason="尚缺必要業務資料")
                                    if self.can_query(state) else record("unavailable", reason="必要資料未取得"))
                continue
            if decision is None:
                records[tid] = record("rejected", reason="證據充足性評估契約失敗")
                continue
            if decision.status == "ready" and not decision.needs:
                try:
                    if slot["kind"] == "direct":
                        # The model evaluates sufficiency, not the factual values.
                        ids = slot["ids"]
                        if decision.source_ids and not set(canonical_ids(decision.source_ids, scope)) <= set(ids):
                            raise ValueError("結構化候選來源與評估不一致。")
                        text, _ = validate_part(host_part(task, slot["answer"], ids, scope), scope)
                        records[tid] = record("draft", response_type="structured", answer=text, source_ids=ids)
                    else:
                        ids = canonical_ids(decision.source_ids, scope)
                        if not set(slot["required"]) <= set(ids):
                            raise ValueError("未採用必要業務來源。")
                        records[tid] = record("ready", response_type="knowledge", source_ids=ids)
                except ValueError as error:
                    records[tid] = record("rejected", reason=str(error))
            elif decision.status == "needs_retrieval":
                needs = self.valid_needs(task, decision.needs, state)
                records[tid] = (record("collecting", needs=needs, reason=decision.reason)
                                if needs and self.can_query(state) else
                                record("no_evidence", reason=decision.reason))
            elif decision.status in ("no_evidence", "clarify", "unavailable") and not decision.needs:
                records[tid] = record(decision.status, reason=decision.reason)
            else:
                records[tid] = record("rejected", reason="證據評估狀態與資料需求矛盾。")
        self.observe("Python 需求狀態", {"階段": "assessment", "狀態": records})
        statuses = {r["status"] for r in records.values()}
        return {"task_states": records, "next_step": "select" if "collecting" in statuses else
                "generate" if "ready" in statuses else "verify"}

    async def generate(self, state):
        self.node(state, "generate")
        records = dict(state["task_states"])
        ready = [t for t in state["tasks"] if records[t["id"]]["status"] == "ready"]
        if ready:
            packet = self.packet(state)
            packet["待生成需求"] = [t["id"] for t in ready]
            drafts = await self.ask(DRAFT_PROMPT, packet, DRAFT_SCHEMA, DraftBundle, "知識回答生成",
                                    lambda p: indexed(p.drafts, [t["id"] for t in ready])) or {}
            for task in ready:
                tid = task["id"]
                draft = drafts.get(tid)
                try:
                    if draft is None or not draft.answer.strip():
                        raise ValueError("缺少有效回答。")
                    scope = self.scope(task, state)
                    ids = canonical_ids(draft.source_ids, scope)
                    required = self.slots(task, state)["required"]
                    if not set(required) <= set(ids):
                        raise ValueError("回答缺少必要業務來源。")
                    text, _ = validate_part(host_part(task, draft.answer, ids, scope), scope)
                    if task["kind"] == "overview":
                        # Data-authored requirements, not per-question prompt rules.
                        clauses = [clause for _, clause in overview_clauses(task, scope) if clause not in text]
                        if clauses:
                            text += "\n\n**重要限制與提醒**\n\n" + "\n".join("- " + c for c in clauses)
                    records[tid] = record("draft", response_type="knowledge", answer=text, source_ids=ids)
                except ValueError as error:
                    records[tid] = record("rejected", reason=str(error))
        return {"task_states": records, "next_step": "verify"}

    async def verify(self, state):
        self.node(state, "verify")
        records = dict(state["task_states"])
        ids = [t["id"] for t in state["tasks"]]
        packet = self.packet(state)
        packet["待驗證需求"] = ids
        # 所有需求都要有 verdict，包括終止限制回覆；不存在「空待驗證清單」
        # 但模型被要求審查七個任務的矛盾。
        verdicts = await self.ask(ANSWER_VERIFY_PROMPT, packet, VERIFY_SCHEMA, AnswerVerification,
                                  "回答語意驗證", lambda p: indexed(p.verdicts, ids))
        if verdicts is None:
            for tid, r in records.items():
                if r["status"] in ("draft", "ready", "completed"):
                    records[tid] = record("rejected", reason="未取得有效語意驗證。")
                elif r["status"] not in ("unsupported", "account_limit"):
                    records[tid] = record("rejected", reason="限制回覆未通過語意驗證。")
            return {"task_states": records, "coverage": "failed", "next_step": "finalize"}
        self.observe("Python 語意驗證結果", {"verdicts": [v.model_dump() for v in verdicts.values()]})
        # 終止狀態錯誤通常是意圖/能力選錯，而非缺少證據。只允許一次重規劃。
        # 先前已完成的業務證據仍存在；預算不重置。
        terminal_issues = {tid: v.issues for tid, v in verdicts.items()
                           if records[tid]["status"] in TERMINAL
                           and records[tid]["status"] != "completed"
                           and (v.verdict != "pass" or v.issues)}
        if terminal_issues and not state["replanned"]:
            return {"feedback": {"限制回覆修正": terminal_issues}, "replanned": True,
                    "coverage": "incomplete", "next_step": "intent"}
        repair, feedback = [], {}
        for tid, r in records.items():
            v = verdicts[tid]
            if v.verdict == "pass" and not v.issues:
                if r["status"] == "draft":
                    records[tid] = record("completed", response_type=r["response_type"],
                                          answer=r["answer"], source_ids=r["source_ids"])
                continue
            if r["status"] in ("draft", "completed") and not state["verification_repair"] and v.issues and (
                    r["response_type"] == "knowledge" or v.verdict == "insufficient"):
                records[tid] = record("pending", reason="待修正具體語意缺失")
                repair.append(tid)
                feedback[tid] = v.issues
            elif r["status"] not in ("unsupported", "account_limit"):
                records[tid] = record("no_evidence" if v.verdict == "insufficient" else "rejected",
                                      reason="; ".join(v.issues))
        if repair:
            return {"task_states": records, "feedback": {"語意修正": feedback},
                    "verification_repair": True, "next_step": "assess"}
        return {"task_states": records, "coverage": "incomplete" if terminal_issues else "complete",
                "next_step": "finalize"}

    async def finalize(self, state):
        self.node(state, "finalize")
        records, texts, checks = state["task_states"], [], []
        for task in state["tasks"]:
            r = records[task["id"]]
            status = r["status"]
            if any(records[d]["status"] != "completed" for d in task["depends_on"]) and status == "completed":
                status = "no_evidence"
                r = record(status, reason="必要依賴需求未完成。")
            if status == "completed":
                try:
                    scope = self.scope(task, state)
                    text, cites = validate_part(host_part(task, r["answer"], r["source_ids"], scope), scope)
                    status = "supported"
                except (ValueError, KeyError):
                    status, text, cites = "rejected", ANSWER_VERIFICATION_FAILED, []
            elif status in TERMINAL:
                text, cites = terminal_message(r), []
            else:
                status, text, cites = "no_evidence", terminal_message(record("no_evidence")), []
            checks.append({"任務ID": task["id"], "問題": task["goal"], "判定": status, "採用段落": cites})
            texts.append(f"**{task['goal']}**\n\n{text}" if len(state["tasks"]) > 1 else text)
        # 同一限制不要因多個無效任務重複輸出；未核定需求不顯示模型自行創造的標題。
        if state["coverage"] != "complete" and checks and not any(c["判定"] == "supported" for c in checks):
            texts = [terminal_message(record("rejected"))]
        if state["coverage"] != "complete":
            texts.append("目前未能確認已涵蓋您的完整需求，請補充或重新詢問。")
        self.observe("Python 檢查回答依據", {"子問題": checks, "參考資料": answer_references(checks, state["evidence"]),
                     "驗證範圍": "逐需求業務契約、本輪證據及模型語意審查；非絕對語意保證",
                     "需求覆蓋": state["coverage"],
                     "判定": "supported" if checks and all(c["判定"] == "supported" for c in checks) else "partial"})
        return {"result": "\n\n".join(texts) or ANSWER_VERIFICATION_FAILED}


def build_intent_graph(runtime):
    graph = StateGraph(IntentState)
    retry = RetryPolicy(max_attempts=2, retry_on=TransientModelError)
    for name in ("intent", "select", "assess", "generate", "verify"):
        graph.add_node(name, getattr(runtime, name), retry_policy=retry, input_schema=IntentState)
    for name in ("prepare_tools", "collect", "finalize"):
        graph.add_node(name, getattr(runtime, name), input_schema=IntentState)
    graph.add_node("tools", runtime.tool_node)
    graph.add_edge(START, "intent")
    graph.add_conditional_edges("intent", runtime.route_next,
                                {"select": "select", "assess": "assess", "verify": "verify", "finalize": "finalize"})
    graph.add_edge("select", "prepare_tools")
    graph.add_conditional_edges("prepare_tools", runtime.route_tools, {"tools": "tools", "collect": "collect"})
    graph.add_edge("tools", "collect")
    graph.add_edge("collect", "assess")
    graph.add_conditional_edges("assess", runtime.route_next, {"select": "select", "generate": "generate", "verify": "verify"})
    graph.add_edge("generate", "verify")
    graph.add_conditional_edges("verify", runtime.route_next, {"intent": "intent", "assess": "assess", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile()
