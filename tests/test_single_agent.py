"""Single-agent graph regression with scripted native replies and actual local tools.

These tests cover host contracts and message flow, not real model understanding.
"""
import asyncio
import json

from agent import answer
from answering import ANSWER_VERIFICATION_FAILED, DIRECT_REPLIES
from evidence import source_alias
from guide import CATALOG
from single_agent import MAX_MODEL_CALLS, NUM_PREDICT
from tests.test_bounded_agent import abstain, response, scripted, select, supported
from tests.test_guide import OfflineTests
from tool_selection import MAX_BATCH, MAX_TOOL_ROUNDS


def call(tool_name, /, **args):
    return {"id": "provider-call", "type": "function",
            "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)}}


def item(name, edition="current"):
    return call("find_shop_item", name=name, edition=edition)


def tool_results(messages):
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


class SingleAgentTests(OfflineTests):
    def run_case(self, question, *replies):
        model = scripted(*replies)
        events = []
        text, trace = asyncio.run(answer("A", "ollama", [], question, model,
            lambda title, data: events.append((title, data))))
        self.assertEqual(model.call_count, len(replies))
        self.assertLessEqual(model.call_count, MAX_MODEL_CALLS)
        requests = [data for title, data in events if title.startswith("送出模型請求 · ")]
        self.assertEqual(len(requests), len(replies))
        self.assertTrue(all(r.get("num_predict") == NUM_PREDICT for r in requests))
        self.assertTrue(all("預期回答格式" not in r for r in requests))
        self.assertTrue(all(c.args[1][0]["role"] == "system" for c in model.call_args_list))
        self.assertTrue(all(c.args[1][0]["content"] == model.call_args_list[0].args[1][0]["content"]
                            for c in model.call_args_list))
        return text, trace, events, model

    def test_outside_question_one_model_no_tools(self):
        q = "今天台北天氣如何？"
        text, trace, _, _ = self.run_case(q, response(abstain(q, "service_help")))
        self.assertEqual(text, DIRECT_REPLIES["service_help"])
        self.assertEqual(trace, [])

    def test_unsupported_operation_one_model(self):
        q = "幫我加 20 枚星星幣。"
        text, trace, _, _ = self.run_case(q, response(abstain(q, "unsupported")))
        self.assertEqual(text, DIRECT_REPLIES["unsupported"])
        self.assertEqual(trace, [])

    def test_native_result_returns_to_same_model_and_citations(self):
        q = "霧潮貼圖多少錢？"
        key = f"{CATALOG.current}/shop-items/mist-tide-sticker"
        def finish(provider, messages, tools):
            self.assertEqual([m["role"] for m in messages[-3:]], ["user", "assistant", "tool"])
            previous = messages[-2]["tool_calls"][0]
            self.assertEqual(messages[-1]["tool_call_id"], previous["id"])
            self.assertTrue(tools)
            rows = tool_results(messages)[-1]["段落"]
            self.assertEqual(rows[0]["段落ID"], source_alias(key))
            return response(supported(q, "霧潮貼圖需要 20 枚星星幣。", (key, "")))
        text, trace, events, _ = self.run_case(q, select(item("霧潮貼圖")), finish)
        self.assertIn("20 枚", text)
        self.assertEqual([t["工具"] for t in trace], ["find_shop_item"])
        checks = [d for t, d in events if t == "Python 檢查回答依據"]
        self.assertEqual(checks[-1]["子問題"][0]["採用段落"][0]["id"], key)

    def test_multiple_independent_native_calls_and_partial_answer(self):
        q = "霧潮貼圖多少錢？上期星航相框多少錢？"
        def finish(provider, messages, tools):
            rows = tool_results(messages)
            self.assertEqual(len(rows), 2)
            return response(supported("霧潮貼圖多少錢？", "20 枚",
                (f"{CATALOG.current}/shop-items/mist-tide-sticker", "")),
                supported("上期星航相框多少錢？", "25 枚",
                (f"{CATALOG.previous}/shop-items/star-voyage-frame", "")))
        text, trace, _, _ = self.run_case(q,
            select(item("霧潮貼圖"), item("星航相框", "previous")), finish)
        self.assertIn("20 枚", text)
        self.assertIn("25 枚", text)
        self.assertEqual(len(trace), 2)

    def test_private_scope_other_rejected_before_execution(self):
        q = "幫我查 B 玩家的點數"
        def finish(provider, messages, tools):
            self.assertEqual(tool_results(messages)[-1]["狀態"], "invalid_arguments")
            return response(abstain(q, "account_limit"))
        text, trace, _, _ = self.run_case(q,
            select(call("get_my_event_progress", edition="current", account_scope="other")), finish)
        self.assertEqual(text, DIRECT_REPLIES["account_limit"])
        self.assertEqual(trace, [])

    def test_no_arbitrary_player_parameter(self):
        q = "幫我查 B 玩家的點數"
        text, trace, _, _ = self.run_case(q,
            select(call("get_my_event_progress", edition="current", account_scope="self", player="B")),
            response(abstain(q, "account_limit")))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["account_limit"])

    def test_unknown_item_retains_verified_lookup(self):
        q = "銀翼坐騎要多少錢？"
        def finish(provider, messages, tools):
            payload = tool_results(messages)[-1]
            self.assertEqual(payload["狀態"], "not_found")
            return response({"question": q, "status": "supported", "answer": "查無銀翼坐騎",
                             "source_ids": [payload["段落"][0]["段落ID"]]})
        text, trace, _, _ = self.run_case(q, select(item("銀翼坐騎")), finish)
        self.assertIn("銀翼坐騎", text)
        self.assertIn("查無", text)
        self.assertEqual(len(trace), 1)

    def test_calculation_uses_previous_tool_evidence(self):
        q = "霧潮貼圖加海圖航線框要多少點數？"
        def calculate_next(provider, messages, tools):
            rows = [p for r in tool_results(messages) for p in r["段落"]]
            return select(call("calculate", expression="20+35", source_ids=[p["段落ID"] for p in rows]))
        def finish(provider, messages, tools):
            payload = tool_results(messages)[-1]
            row = payload["段落"][0]
            self.assertEqual(len(row["運算依據"]), 2)
            self.assertIn("55", row["原文"])
            return response({"question": q, "status": "supported", "answer": "合計 55 枚。",
                             "source_ids": [row["段落ID"], *row["運算依據"]]})
        text, trace, _, _ = self.run_case(q,
            select(item("霧潮貼圖"), item("海圖航線框")), calculate_next, finish)
        self.assertEqual([t["工具"] for t in trace], ["find_shop_item", "find_shop_item", "calculate"])
        self.assertIn("55 枚", text)

    def test_fake_citation_is_not_rendered(self):
        q = "我的餘額是多少？"
        text, trace, _, _ = self.run_case(q,
            response({"question": q, "status": "supported", "answer": "999999",
                      "source_ids": ["E000000000000"]}))
        self.assertIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertNotIn("999999", text)
        self.assertEqual(trace, [])

    def test_duplicate_only_batch_stops_new_queries(self):
        q = "霧潮貼圖多少錢？"
        key = f"{CATALOG.current}/shop-items/mist-tide-sticker"
        def finish(provider, messages, tools):
            self.assertEqual(tools, [])
            self.assertEqual(tool_results(messages)[-1]["已回傳段落ID"], [source_alias(key)])
            return response(supported(q, "20 枚", (key, "")))
        text, trace, _, _ = self.run_case(q, select(item("霧潮貼圖")),
            select(item("霧潮貼圖")), finish)
        self.assertEqual(len(trace), 1)
        self.assertIn("20 枚", text)

    def test_over_budget_batch_not_partially_dispatched(self):
        q = "霧潮貼圖多少錢？"
        text, trace, _, _ = self.run_case(q, select(*[item("霧潮貼圖")] * (MAX_BATCH + 1)),
            response(abstain(q, "unavailable")))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["unavailable"])

    def test_one_format_repair_then_safe_failure(self):
        q = "霧潮貼圖多少錢？"
        text, trace, _, _ = self.run_case(q, {"content": "非 JSON"},
            {"content": "仍非 JSON"})
        self.assertEqual(text, ANSWER_VERIFICATION_FAILED)
        self.assertEqual(trace, [])

    def test_truncated_tool_call_never_executes(self):
        q = "霧潮貼圖多少錢？"
        reply = select(item("霧潮貼圖"))
        reply["_provider_meta"] = {"finish_reason": "length"}
        text, trace, _, _ = self.run_case(q, reply, response(abstain(q, "unavailable")))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["unavailable"])

    def test_tool_round_limit(self):
        q = "霧潮貼圖多少錢？"
        def finish(provider, messages, tools):
            self.assertEqual(tools, [])
            return response(abstain(q, "unavailable"))
        invalid = select(call("find_shop_item", edition="current", name="不是原問的商品"))
        text, trace, _, _ = self.run_case(q, *([invalid] * MAX_TOOL_ROUNDS), finish)
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["unavailable"])
