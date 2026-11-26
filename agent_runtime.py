"""Request-local LangGraph nodes; no model output is execution authority."""
from __future__ import annotations

import asyncio
import json
import os
from typing import TypedDict

from langchain_core.messages import (AIMessage, HumanMessage, ToolMessage,
                                     convert_to_messages, convert_to_openai_messages)
from langchain_core.tools import StructuredTool
from langgraph.graph import MessagesState
from langgraph.prebuilt import ToolNode

from answering import (ANSWER_PROMPT, ANSWER_SCHEMA,
                       ANSWER_VERIFICATION_FAILED, decode_response, render_response)
from answer_quality import review_answer
from backend import ToolRegistry, compact_json, evidence_for
from evidence import model_sources, source_map
from model_client import model_step
from request_context import RequestContext
from tool_selection import AGENT_PROMPT, MAX_TOOL_CALLS, MAX_TOOL_ROUNDS, normalized_calls


STOP_RECHECK_REASON = (
    "先前回答標記證據不足，卻同時提供文字與本輪有效引用。請依玩家原問重新核對"
    "缺少的必要資料，例如明示對象的存在性、狀態或適用條件；只選能補足缺口的新工具。"
    "若已查無、需要玩家補充或沒有適用工具，停止查詢。不得用其他對象代替。"
)


class SupportState(MessagesState, total=False):
    # messages 是選擇階段的模型訊息；dispatch_messages 只包含主機核准的呼叫。
    dispatch_messages: list
    calls: list
    plans: list
    sources: list
    evidence: dict
    cache: dict
    errors: list
    trace: list
    attempts: int
    round_number: int
    repair_available: bool
    selection_retry_used: bool
    progress: bool
    can_retrieve: bool
    packet: dict
    data: dict | None
    previous_answer: dict | None
    stop_candidates: list
    stop_used: bool
    stop_pending: bool
    unresolved: bool
    review_allowed: bool
    next_step: str
    result: str


def build_answer_packet(context, question, sources, errors, trace, can_retrieve, definitions, *, compact=True):
    return {"服務上下文": context, "玩家原問": question,
            "工具結果": model_sources(sources, aliases=True, compact=compact), "工具錯誤": errors,
            "允許補查": can_retrieve,
            "可用資料工具": [{"name": t["function"]["name"], "description": t["function"]["description"]}
                            for t in definitions],
            "已執行查詢": [{"工具": t["工具"], "參數": t["參數"]} for t in trace]}


def raw_arguments(call):
    return json.loads(call["function"]["arguments"])


def native_call(call, arguments):
    return {"name": call["function"]["name"], "args": arguments,
            "id": call["id"], "type": "tool_call"}


class TurnRuntime:
    """Dependencies live outside graph state; each answer owns one runtime and registry."""

    def __init__(self, player, provider, history, question, complete, observe, *, stop_mode=None):
        self.provider, self.question = provider, question
        self.complete, self.observe = complete, observe
        self.context = RequestContext.build(question, history)
        self.registry = ToolRegistry(player)
        self.definitions = self.registry.definitions()
        self.stop_mode = (os.getenv("EVENT_SUPPORT_AGENT_STOP_GATE", "auto").strip().lower()
                          if stop_mode is None else stop_mode)
        if self.stop_mode not in ("auto", "off"):
            raise ValueError("EVENT_SUPPORT_AGENT_STOP_GATE 請填 auto 或 off。")
        self.conversation = [*self.context.history, {"role": "user", "content": question}]
        # ToolNode 使用同一份工具登錄；不向模型暴露玩家 ID 或主機正規化欄位。
        self.tool_node = ToolNode(
            [self.make_tool(d["function"]) for d in self.definitions],
            messages_key="dispatch_messages", handle_tool_errors=False)
        self.observe("Python 工具介接模式", {"服務": provider, "模式": "native"})
        self.observe("Python Agent Harness", {"框架": "langgraph", "停止檢查": self.stop_mode})

    def make_tool(self, definition):
        name = definition["name"]

        async def execute(**args):
            # 只接受 prepare_tools 節點通過 registry.validate 的主機參數。
            try:
                result = await asyncio.to_thread(self.registry.execute, name, args)
                source, refs, withheld = evidence_for(name, args, result, self.question)
                # 保留實際核准查詢對象及範圍，供逐需求槽位比對；不是模型自行填寫的事實。
                source["查詢參數"] = dict(args)
                return compact_json(source), {"source": source, "raw": result,
                                               "refs": refs, "withheld": withheld}
            except Exception as error:
                payload = {"工具": name, "檔期": args.get("edition", ""), "狀態": "tool_error",
                           "原因": "資料工具未能完成查詢。"}
                return compact_json(payload), {"source": payload, "error": type(error).__name__}

        # dict schema 保留單一 registry 定義；權限及嚴格參數驗證在派發前完成。
        return StructuredTool.from_function(
            coroutine=execute, name=name, description=definition["description"],
            args_schema=definition["parameters"], infer_schema=False,
            response_format="content_and_artifact")

    def initial_state(self):
        selection = [{"role": "system", "content": AGENT_PROMPT + "\n服務上下文："
                      + compact_json(self.context.public_summary())}, *self.conversation]
        return {"messages": convert_to_messages(selection), "dispatch_messages": [],
                "calls": [], "plans": [], "sources": [], "evidence": {}, "cache": {},
                "errors": [], "trace": [], "attempts": 0, "round_number": 0,
                "repair_available": True, "selection_retry_used": False,
                "progress": False, "can_retrieve": False, "packet": {}, "data": None,
                "previous_answer": None, "stop_candidates": [], "stop_used": False,
                "stop_pending": False, "unresolved": False, "review_allowed": True,
                "next_step": "select"}

    def node(self, state, name):
        self.observe("Python Harness 節點", {"節點": name, "輪次": state["round_number"],
                     "工具嘗試": state["attempts"], "有效證據數": len(state["evidence"])})

    async def select(self, state: SupportState):
        number = state["round_number"] + 1
        if number > MAX_TOOL_ROUNDS:
            raise RuntimeError("Harness 超出工具輪數上限。")
        self.node({**state, "round_number": number}, "select")
        stage = "選擇資料工具" if number == 1 else "補查資料工具"
        messages = list(state["messages"])
        additions = []
        retried = state["selection_retry_used"]
        errors = list(state["errors"])
        while True:
            reply = await model_step(self.provider, convert_to_openai_messages(messages),
                                     self.definitions, self.complete, self.observe, stage)
            calls = normalized_calls(reply, number)
            text = reply.get("content")
            if calls or isinstance(text, str) and text.strip():
                break
            self.observe("Python 模型回傳空工具選擇",
                         {"輪次": number, "回傳資訊": reply.get("_provider_meta", {})})
            if retried or state["stop_pending"]:
                errors.append({"狀態": "selection_error", "原因": "模型未回傳工具選擇。"})
                break
            retried = True
            stage = "重試資料工具"
            feedback = HumanMessage(content="前次沒有回傳工具或文字。需要查詢時請呼叫工具，無需查詢時請以文字說明停止。")
            messages.append(feedback)
            additions.append(feedback)
        return {"round_number": number, "calls": calls, "messages": additions,
                "selection_retry_used": retried, "errors": errors}

    def validate_call(self, state, name, raw):
        return self.registry.validate(name, raw, self.context, state["evidence"])

    async def prepare_tools(self, state: SupportState):
        self.node(state, "execute")
        calls = state["calls"]
        self.observe("Python 工具批次", {"輪次": state["round_number"],
                     "請求工具": [c["function"]["name"] for c in calls]})
        attempts, plans, dispatch, history_calls = state["attempts"], [], [], []
        batch_keys = {}
        for call in calls:
            name, cid = call["function"]["name"], call["id"]
            try:
                raw = raw_arguments(call)
            except (TypeError, ValueError, KeyError):
                raw = None
            # 即使無效或超額也有合法的 ToolMessage 對應，不能執行其參數。
            history_calls.append(native_call(call, raw if isinstance(raw, dict) else {}))
            plan = {"id": cid, "name": name}
            plans.append(plan)
            if attempts >= MAX_TOOL_CALLS:
                plan["payload"] = {"狀態": "budget_exhausted", "原因": "已達本輪工具上限。"}
                continue
            attempts += 1
            try:
                args = self.validate_call(state, name, raw)
            except (KeyError, TypeError, ValueError) as error:
                plan["payload"] = {"工具": name, "狀態": "invalid_arguments", "原因": str(error)}
                continue
            key = compact_json([name, args])
            plan.update(key=key, args=args)
            if key in state["cache"]:
                plan["payload"] = state["cache"][key]
                plan["reused"] = True
            elif key in batch_keys:
                plan["leader"] = batch_keys[key]
                plan["reused"] = True
            else:
                batch_keys[key] = cid
                plan["leader"] = cid
                dispatch.append(native_call(call, args))
                self.observe(f"呼叫工具 · {name}", dict(args))
        return {"plans": plans, "attempts": attempts,
                "messages": [AIMessage(content="", tool_calls=history_calls)] if calls else [],
                "dispatch_messages": [AIMessage(content="", tool_calls=dispatch)] if dispatch else []}

    def route_tools(self, state):
        return "tools" if state["dispatch_messages"] else "collect"

    async def collect(self, state: SupportState):
        # ToolNode 平行執行；由單一節點按原呼叫順序合併證據及快取，避免競態。
        outputs = {message.tool_call_id: message for message in state["dispatch_messages"]
                   if isinstance(message, ToolMessage)}
        sources, trace, errors = (list(state[k]) for k in ("sources", "trace", "errors"))
        evidence, cache = dict(state["evidence"]), dict(state["cache"])
        seen = set(evidence)
        additions, progress = [], False
        for plan in state["plans"]:
            name, cid = plan["name"], plan["id"]
            payload = plan.get("payload")
            if plan.get("reused"):
                self.observe("Python 重用工具結果", {"工具": name, "參數": plan["args"]})
            if payload is None:
                message = outputs.get(plan["leader"])
                artifact = getattr(message, "artifact", None)
                if not isinstance(artifact, dict) or "source" not in artifact:
                    raise RuntimeError("ToolNode 未回傳可核對的工具結果。")
                payload = artifact["source"]
                if not plan.get("reused"):
                    cache[plan["key"]] = payload
                    if artifact.get("error"):
                        errors.append(payload)
                        self.observe(f"收到工具回傳 · {name}", {"isError": True,
                                     "錯誤類型": artifact["error"]})
                    else:
                        raw = artifact["raw"]
                        blocks = raw if isinstance(raw, list) else [raw]
                        content = [json.dumps(block, ensure_ascii=False, indent=2) for block in blocks]
                        self.observe(f"收到工具回傳 · {name}", {"isError": False, "內容": content,
                                     "結構化資料": {"result": raw} if isinstance(raw, list) else raw})
                        if artifact["withheld"]:
                            self.observe("Python 核對公告適用條件", {"未採用段落": artifact["withheld"]})
                        trace.append({"工具": name, "參數": dict(plan["args"]),
                                      "結果": "\n".join(content), "商品參照": artifact["refs"]})
                        sources.append(payload)
                        for row in payload["段落"]:
                            evidence.setdefault(row["段落ID"], row)
                        progress = True
            elif not plan.get("reused") and payload.get("狀態") == "invalid_arguments":
                errors.append(payload)
                self.observe("Python 工具驗證失敗", payload)
            compact = model_sources([payload], seen=seen)[0] if "段落" in payload else payload
            additions.append(ToolMessage(content=compact_json(compact), tool_call_id=cid, name=name))
        updates = {"messages": additions, "dispatch_messages": [], "sources": sources,
                   "trace": trace, "errors": errors, "evidence": evidence, "cache": cache,
                   "progress": progress, "stop_pending": False, "next_step": "assess"}
        if state["stop_pending"] and not progress:
            self.observe("Python 停止檢查結果", {"判定": "no_progress",
                         "原因": "沒有新的成功查詢，保留先前安全回答。"})
            updates.update(data=state["previous_answer"], review_allowed=False, next_step="finalize")
        return updates

    def route_collect(self, state):
        return state["next_step"]

    async def assess(self, state: SupportState):
        self.node(state, "assess")
        can_retrieve = (state["round_number"] < MAX_TOOL_ROUNDS and state["attempts"] < MAX_TOOL_CALLS
                        and (state["round_number"] == 1 or state["progress"]))
        packet = build_answer_packet(self.context.public_summary(), self.question, state["sources"],
                                     state["errors"], state["trace"], can_retrieve, self.definitions)
        self.observe("Python 回答上下文", {"來源對照": source_map(state["evidence"]),
                     "證據數": len(state["evidence"]), "模型資料字元數": len(compact_json(packet))})
        messages = [{"role": "system", "content": ANSWER_PROMPT}, *self.conversation[:-1],
                    {"role": "user", "content": compact_json(packet)}]
        data, candidates, repair_available = None, [], state["repair_available"]
        for retry in range(2):
            candidates = []
            reply = await model_step(self.provider, messages, [], self.complete, self.observe,
                                     "結構化回答" if not retry else "修正回答格式",
                                     response_schema=ANSWER_SCHEMA)
            try:
                data = decode_response(reply.get("content"), observe=self.observe,
                                       evidence=state["evidence"], original_question=self.question,
                                       stop_candidates=candidates)
                if retry:
                    self.observe("Python 回答格式修正完成", {"判定": "format_valid"})
                break
            except ValueError as error:
                self.observe("Python 回答格式驗證失敗", {"原因": str(error)})
                if not repair_available:
                    break
                repair_available = False
                # 保留原始草稿和實際錯誤，避免無提示地重複生成。
                feedback = {"錯誤": str(error), "前次輸出": reply.get("content")}
                messages[-1] = {"role": "user", "content": compact_json(
                    {**packet, "修正回饋": feedback})}
        return {"packet": packet, "data": data, "stop_candidates": candidates,
                "repair_available": repair_available, "can_retrieve": can_retrieve}

    def more(self, state, reason):
        self.observe("Python 接受補查需求", {"缺少資料": reason,
                     "剩餘工具輪數": MAX_TOOL_ROUNDS - state["round_number"]})
        feedback = HumanMessage(content=compact_json({
            "系統補查回饋": reason, "說明": "這是證據評估，不是玩家新增的事實。"
            "請依原問與現有工具結果選有意義的下一個查詢。"}))
        return {"previous_answer": state["data"], "messages": [feedback], "next_step": "select"}

    async def stop_gate(self, state: SupportState):
        self.node(state, "stop_gate")
        data = state["data"]
        if data is None:
            return {"data": state["previous_answer"], "unresolved": state["previous_answer"] is not None,
                    "review_allowed": False, "next_step": "finalize"}
        parts = data["results"]
        unqueried = not state["sources"] and any(p["status"] == "no_evidence" for p in parts)
        if unqueried and state["can_retrieve"] and not state["selection_retry_used"]:
            return {**self.more(state, "尚未取得任何資料工具結果，不可將未查詢當成查無依據。"),
                    "selection_retry_used": True}
        if unqueried:
            data = {**data, "results": [{**p, "status": "unavailable"} if p["status"] == "no_evidence"
                                       else p for p in parts]}
            parts = data["results"]
            self.observe("Python 未取得工具資料", {"原因": "本輪沒有成功的工具查詢。"})
        if data["action"] == "retrieve":
            if state["can_retrieve"]:
                return self.more({**state, "data": data}, data["missing"])
            self.observe("Python 停止補查", {"原因": "已達上限或未取得新資料。"})
            return {"data": data, "next_step": "finalize"}
        if (self.stop_mode == "auto" and not state["stop_used"] and state["sources"]
                and state["can_retrieve"] and len(parts) == 1
                and parts[0]["status"] == "no_evidence" and state["stop_candidates"]):
            self.observe("Python 停止檢查", {"判定": "recheck", "線索": state["stop_candidates"],
                         "原因": STOP_RECHECK_REASON})
            return {**self.more({**state, "data": data}, STOP_RECHECK_REASON),
                    "stop_used": True, "stop_pending": True}
        return {"data": data, "next_step": "finalize"}

    def route_stop(self, state):
        return state["next_step"]

    async def finalize(self, state: SupportState):
        self.node(state, "finalize")
        data = state["data"]
        if data is None:
            return {"result": ANSWER_VERIFICATION_FAILED}
        if state["review_allowed"] and data["action"] == "answer":
            data = await review_answer(self.provider, data, state["evidence"], state["packet"],
                                       self.conversation, self.complete, self.observe, sources=state["sources"])
        return {"data": data, "result": render_response(
            data, state["evidence"], self.observe, unresolved=state["unresolved"])}
