"""Request-local execution state. Model output is never execution authority."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Callable

from answering import (ANSWER_PROMPT, ANSWER_SCHEMA, ANSWER_VERIFICATION_FAILED,
                       decode_response, render_response)
from answer_quality import review_answer
from backend import ToolRegistry, compact_json, evidence_for
from model_client import model_step
from request_context import RequestContext
from tool_selection import (AGENT_PROMPT, MAX_TOOL_CALLS, MAX_TOOL_ROUNDS,
                            normalized_calls)


STOP_RECHECK_REASON = (
    "先前回答標記證據不足，卻同時提供文字與本輪有效引用。請依玩家原問重新核對"
    "缺少的必要資料，例如明示對象的存在性、狀態或適用條件；只選能補足缺口的新工具。"
    "若已查無、需要玩家補充或沒有適用工具，停止查詢。不得用其他對象代替。"
)


@dataclass
# 每輪請求的執行狀態；保存已驗證證據、工具預算、補查與審查狀態。
class TurnState:
    player: str
    provider: str
    history: list
    question: str
    complete: Callable
    observe: Callable
    context: RequestContext = field(init=False)
    registry: ToolRegistry = field(init=False)
    tools: list = field(init=False)
    stop_mode: str = field(init=False)
    conversation: list = field(init=False)
    selection: list = field(init=False)
    sources: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    cache: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    attempts: int = 0
    round_number: int = 0
    repair_available: bool = True
    previous_answer: dict | None = None
    selection_retry_used: bool = False
    calls: list = field(default_factory=list)
    progress: bool = False
    can_retrieve: bool = False
    packet: dict = field(default_factory=dict)
    data: dict | None = None
    stop_candidates: list = field(default_factory=list)
    stop_used: bool = False
    stop_pending: bool = False
    unresolved: bool = False
    review_allowed: bool = True

    def __post_init__(self):
        self.context = RequestContext.build(self.question, self.history)
        self.registry = ToolRegistry(self.player)
        self.tools = self.registry.definitions()
        self.stop_mode = os.getenv("EVENT_SUPPORT_AGENT_STOP_GATE", "auto").strip().lower()
        if self.stop_mode not in ("auto", "off"):
            raise ValueError("EVENT_SUPPORT_AGENT_STOP_GATE 請填 auto 或 off。")
        self.conversation = [*self.context.history, {"role": "user", "content": self.question}]
        self.selection = [{"role": "system", "content": AGENT_PROMPT + "\n服務上下文："
                           + compact_json(self.context.public_summary())}, *self.conversation]
        self.observe("Python 工具介接模式", {"服務": self.provider, "模式": "native"})
        self.observe("Python Agent Harness", {"框架": "pydantic-graph", "停止檢查": self.stop_mode})

    def node(self, name):
        self.observe("Python Harness 節點", {"節點": name, "輪次": self.round_number,
                     "工具嘗試": self.attempts, "有效證據數": len(self.evidence)})

    # 模型提出工具請求，兩個供應商共用原生呼叫及後續驗證；選擇不等於授權。
    async def select_tools(self):
        self.round_number += 1
        if self.round_number > MAX_TOOL_ROUNDS:
            raise RuntimeError("Harness 超出工具輪數上限。")
        self.node("select")
        stage = "選擇資料工具" if self.round_number == 1 else "補查資料工具"
        while True:
            selected = await model_step(self.provider, self.selection, self.tools,
                                        self.complete, self.observe, stage)
            self.calls = normalized_calls(selected, self.round_number)
            content = selected.get("content")
            if self.calls or (isinstance(content, str) and content.strip()):
                return
            self.observe("Python 模型回傳空工具選擇", {"輪次": self.round_number,
                         "回傳資訊": selected.get("_provider_meta", {})})
            if self.selection_retry_used or self.stop_pending:
                self.errors.append({"狀態": "selection_error", "原因": "模型未回傳工具選擇。"})
                return
            self.selection_retry_used = True
            stage = "重試資料工具"
            self.selection.append({"role": "user", "content":
                "前次沒有回傳工具或文字。需要查詢時請呼叫工具，無需查詢時請以文字說明停止。"})

    # 先驗證參數及可信指涉，再查詢；相同正規化參數重用本輪結果。
    async def execute_call(self, call):
        name = call["function"]["name"]
        try:
            raw = json.loads(call["function"]["arguments"])
            args = self.registry.validate(name, raw, self.context, self.evidence)
        except (KeyError, TypeError, ValueError) as error:
            payload = {"工具": name, "狀態": "invalid_arguments", "原因": str(error)}
            self.errors.append(payload)
            self.observe("Python 工具驗證失敗", payload)
            return payload, False
        key = (name, compact_json(args))
        if key in self.cache:
            self.observe("Python 重用工具結果", {"工具": name, "參數": args})
            return self.cache[key], False
        self.observe(f"呼叫工具 · {name}", dict(args))
        try:
            result = await asyncio.to_thread(self.registry.execute, name, args)
            source, refs, withheld = evidence_for(name, args, result, self.question)
        except Exception as error:
            payload = {"工具": name, "檔期": args.get("edition", ""), "狀態": "tool_error",
                       "原因": "資料工具未能完成查詢。"}
            self.errors.append(payload)
            self.observe(f"收到工具回傳 · {name}", {"isError": True, "錯誤類型": type(error).__name__})
            self.cache[key] = payload
            return payload, False
        blocks = result if isinstance(result, list) else [result]
        content = [json.dumps(block, ensure_ascii=False, indent=2) for block in blocks]
        self.observe(f"收到工具回傳 · {name}", {"isError": False, "內容": content,
                "結構化資料": {"result": result} if isinstance(result, list) else result})
        if withheld:
            self.observe("Python 核對公告適用條件", {"未採用段落": withheld})
        self.trace.append({"工具": name, "參數": dict(args), "結果": "\n".join(content),
                           "商品參照": refs})
        self.cache[key] = source
        self.sources.append(source)
        for row in source["段落"]:
            self.evidence[row["段落ID"]] = row
        return source, True

    # 每次嘗試都計入預算，補查沒有新成功結果時回到安全的前次回答。
    async def execute_batch(self):
        self.node("execute")
        self.observe("Python 工具批次", {"輪次": self.round_number,
                     "請求工具": [c["function"]["name"] for c in self.calls]})
        self.progress = False
        if self.calls:
            self.selection.append({"role": "assistant", "content": "", "tool_calls": self.calls})
            for call in self.calls:
                if self.attempts >= MAX_TOOL_CALLS:
                    payload = {"狀態": "budget_exhausted", "原因": "已達本輪工具上限。"}
                else:
                    self.attempts += 1
                    payload, changed = await self.execute_call(call)
                    self.progress = self.progress or changed
                self.selection.append({"role": "tool", "tool_call_id": call["id"],
                                       "content": compact_json(payload)})
        if self.stop_pending:
            self.stop_pending = False
            if not self.progress:
                # A recheck must not churn on identical calls or no tools.
                self.observe("Python 停止檢查結果", {"判定": "no_progress",
                             "原因": "沒有新的成功查詢，保留先前安全回答。"})
                self.data = self.previous_answer
                self.review_allowed = False
                return False
        return True

    # 使用本輪證據及原問題生成結構化結果；格式修復機會全輪有界。
    async def assess_evidence(self):
        self.node("assess")
        self.can_retrieve = (self.round_number < MAX_TOOL_ROUNDS and self.attempts < MAX_TOOL_CALLS
                             and (self.round_number == 1 or self.progress))
        self.packet = {"服務上下文": self.context.public_summary(), "玩家原問": self.question,
                       "工具結果": self.sources, "工具錯誤": self.errors,
                       "允許補查": self.can_retrieve,
                       "可用資料工具": [{"name": t["function"]["name"],
                                       "description": t["function"]["description"]} for t in self.tools],
                       "已執行查詢": [{"工具": row["工具"], "參數": row["參數"]} for row in self.trace]}
        messages = [{"role": "system", "content": ANSWER_PROMPT + "\n回答格式：" + compact_json(ANSWER_SCHEMA)},
                    *self.conversation, {"role": "user", "content": compact_json(self.packet)}]
        self.data = None
        self.stop_candidates = []
        for retry in range(2):
            candidates = []
            try:
                reply = await model_step(self.provider, messages, [], self.complete, self.observe,
                                         "結構化回答" if not retry else "修正回答格式",
                                         response_schema=ANSWER_SCHEMA)
                self.data = decode_response(reply.get("content"), observe=self.observe,
                    evidence=self.evidence, original_question=self.question, stop_candidates=candidates)
                self.stop_candidates = candidates
                if retry:
                    self.observe("Python 回答格式修正完成", {"判定": "format_valid"})
                break
            except ValueError as error:
                self.observe("Python 回答格式驗證失敗", {"原因": str(error)})
                if not self.repair_available:
                    break
                self.repair_available = False
                messages[0] = {"role": "system", "content": ANSWER_PROMPT
                              + "\n回答格式：" + compact_json(ANSWER_SCHEMA)
                              + "\n前次輸出不符合契約，請重新生成完整 JSON。"}

    # 補查原因屬系統回饋，不得成為新的玩家事實或可引用證據。
    def request_more(self, reason):
        self.previous_answer = self.data
        self.observe("Python 接受補查需求", {"缺少資料": reason,
                     "剩餘工具輪數": MAX_TOOL_ROUNDS - self.round_number})
        self.selection.append({"role": "user", "content": compact_json({
            "系統補查回饋": reason, "說明": "這是證據評估，不是玩家新增的事實。"
            "請依原問與現有工具結果選有意義的下一個查詢。"})})

    # 決定回到工具選擇或結束；矛盾拒答最多重新檢查一次，不直接提升回答狀態。
    def stop_gate(self):
        """Return True only when another bounded selection is warranted.

        Inspired by a Stop hook, not a claim that a model's citations prove
        entailment. It grants a single opportunity to recheck a contradictory
        single abstention. It never authorizes a tool or promotes claims.
        """
        self.node("stop_gate")
        if self.data is None:
            self.data = self.previous_answer
            self.unresolved = self.data is not None
            self.review_allowed = False
            return False
        parts = self.data["results"]
        unqueried = not self.sources and any(p["status"] == "no_evidence" for p in parts)
        if unqueried and self.can_retrieve and not self.selection_retry_used:
            self.selection_retry_used = True
            self.request_more("尚未取得任何資料工具結果，不可將未查詢當成查無依據。")
            return True
        if unqueried:
            for part in parts:
                if part["status"] == "no_evidence":
                    part["status"] = "unavailable"
            self.observe("Python 未取得工具資料", {"原因": "本輪沒有成功的工具查詢。"})
        if self.data["action"] == "retrieve":
            if self.can_retrieve:
                self.request_more(self.data["missing"])
                return True
            self.observe("Python 停止補查", {"原因": "已達上限或未取得新資料。"})
            return False
        # Restrict to a single unresolved demand: do not rewrite independently
        # supported answers or account/scope refusals to rescue another part.
        if (self.stop_mode == "auto" and not self.stop_used and self.sources
                and self.can_retrieve and len(parts) == 1
                and parts[0]["status"] == "no_evidence" and self.stop_candidates):
            self.stop_used = self.stop_pending = True
            self.observe("Python 停止檢查", {"判定": "recheck",
                         "線索": self.stop_candidates, "原因": STOP_RECHECK_REASON})
            self.request_more(STOP_RECHECK_REASON)
            return True
        return False

    # 工具流程已停止，才允許有界審查與最終引用驗證；審查不重新開放工具。
    async def finalize(self):
        self.node("finalize")
        if self.data is None:
            return ANSWER_VERIFICATION_FAILED, self.trace
        if self.review_allowed and self.data["action"] == "answer":
            self.data = await review_answer(self.provider, self.data, self.evidence,
                self.packet, self.conversation, self.complete, self.observe)
        return render_response(self.data, self.evidence, self.observe,
                               unresolved=self.unresolved), self.trace
