"""Single-agent graph regression with scripted native replies and actual local tools.

These tests cover host contracts and message flow, not real model understanding.
"""
import asyncio
import json
from unittest.mock import patch

from agent import answer
from backend import ToolRegistry, evidence_for
from answering import validate_part
from answering import ANSWER_VERIFICATION_FAILED, DIRECT_REPLIES
from evidence import source_alias
from guide import CATALOG
from single_agent import MAX_MODEL_CALLS, NUM_PREDICT
from tests.test_bounded_agent import abstain, scripted, select, supported
from single_output import FINAL_SCHEMA, FINAL_TOOL
from tests.test_guide import OfflineTests
from tool_selection import MAX_BATCH, MAX_TOOL_ROUNDS


def call(tool_name, /, **args):
    return {"id": "provider-call", "type": "function",
            "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)}}


def response(*parts):
    return select(call(FINAL_TOOL, results=list(parts)))


def structured(*parts):
    return {"content": json.dumps({"results": list(parts)}, ensure_ascii=False)}


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
        self.assertTrue(all(r.get("預期回答格式") == FINAL_SCHEMA if not r["tools"]
                            else "預期回答格式" not in r for r in requests))
        self.assertTrue(all(FINAL_TOOL in [t["function"]["name"] for t in r["tools"]]
                            for r in requests if r["tools"]))
        self.assertTrue(all(c.args[1][0]["role"] == "system" for c in model.call_args_list))
        base_system = model.call_args_list[0].args[1][0]["content"]
        self.assertTrue(all(c.args[1][0]["content"].startswith(base_system)
                            for c in model.call_args_list))
        self.assertTrue(all([m["content"] for m in c.args[1] if m["role"] == "user"] == [question]
                            for c in model.call_args_list))
        metrics = [data for title, data in events if title.startswith("Python 模型請求指標 · ")]
        self.assertEqual(len(metrics), len(replies))
        self.assertTrue(all(m["耗時毫秒"] >= 0 and m["訊息數"] == len(c.args[1])
                            for m, c in zip(metrics, model.call_args_list)))
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

    def test_computed_source_only_expands_dependencies_without_another_model_call(self):
        q = "霧潮貼圖加海圖航線框要多少點數？"
        def calculate_next(provider, messages, tools):
            rows = [p for r in tool_results(messages) for p in r["段落"]]
            return select(call("calculate", expression="20+35",
                               source_ids=[p["段落ID"] for p in rows]))
        def finish(provider, messages, tools):
            row = tool_results(messages)[-1]["段落"][0]
            return response({"question": q, "status": "supported", "answer": "合計 55 枚。",
                             "source_ids": [row["段落ID"]]})
        text, trace, events, model = self.run_case(q,
            select(item("霧潮貼圖"), item("海圖航線框")), calculate_next, finish)
        self.assertEqual(model.call_count, 3)
        self.assertEqual([t["工具"] for t in trace], ["find_shop_item", "find_shop_item", "calculate"])
        self.assertIn("55 枚", text)
        checks = [d for title, d in events if title == "Python 檢查回答依據"][-1]
        ids = [c["id"] for c in checks["子問題"][0]["採用段落"]]
        self.assertEqual(len(ids), 3)
        self.assertEqual(set(ids[1:]), {f"{CATALOG.current}/shop-items/mist-tide-sticker",
                                        f"{CATALOG.current}/shop-items/sea-chart-frame"})
        self.assertEqual(checks["判定"], "supported")
        self.assertTrue(any(title == "Python 展開證據依賴" for title, _ in events))

    def test_fake_citation_is_not_rendered(self):
        q = "我的餘額是多少？"
        invalid = {"question": q, "status": "supported", "answer": "999999",
                   "source_ids": ["E000000000000"]}
        text, trace, _, _ = self.run_case(q, response(invalid), structured(invalid))
        self.assertIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertNotIn("999999", text)
        self.assertEqual(trace, [])

    def test_duplicate_only_batch_stops_new_queries(self):
        q = "霧潮貼圖多少錢？"
        key = f"{CATALOG.current}/shop-items/mist-tide-sticker"
        def finish(provider, messages, tools, *, response_schema):
            self.assertEqual(tools, [])
            self.assertEqual(response_schema, FINAL_SCHEMA)
            self.assertEqual(tool_results(messages)[-1]["已回傳段落ID"], [source_alias(key)])
            return structured(supported(q, "20 枚", (key, "")))
        text, trace, _, _ = self.run_case(q, select(item("霧潮貼圖")),
            select(item("霧潮貼圖")), finish)
        self.assertEqual(len(trace), 1)
        self.assertIn("20 枚", text)

    def test_over_budget_batch_not_partially_dispatched(self):
        q = "霧潮貼圖多少錢？"
        text, trace, _, _ = self.run_case(q, select(*[item("霧潮貼圖")] * (MAX_BATCH + 1)),
            response(abstain(q, "no_evidence")))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["no_evidence"])

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
        text, trace, _, _ = self.run_case(q, reply, response(abstain(q, "no_evidence")))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["no_evidence"])

    def test_tool_round_limit(self):
        q = "霧潮貼圖多少錢？"
        def finish(provider, messages, tools, *, response_schema):
            self.assertEqual(tools, [])
            self.assertEqual(response_schema, FINAL_SCHEMA)
            return structured(abstain(q, "no_evidence"))
        invalid = select(call("find_shop_item", edition="current", name="不是原問的商品"))
        text, trace, _, _ = self.run_case(q, *([invalid] * MAX_TOOL_ROUNDS), finish)
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["no_evidence"])

    def test_malformed_text_falls_back_to_provider_schema_once(self):
        q = "幫我查其他玩家的餘額"
        malformed = {"content": '{"results":[{"question":"查其他玩家","status":"account_limit","answer":"","source_ids":[]}]}}'}
        def finish(provider, messages, tools, *, response_schema):
            self.assertEqual(tools, [])
            self.assertEqual(response_schema, FINAL_SCHEMA)
            return structured(abstain(q, "account_limit"))
        text, trace, _, _ = self.run_case(q, malformed, finish)
        self.assertEqual(text, DIRECT_REPLIES["account_limit"])
        self.assertEqual(trace, [])

    def test_mixed_terminal_and_data_batch_executes_valid_queries_and_rejects_submission(self):
        q = "霧潮貼圖多少錢？上期星航相框多少錢？"
        def finish(provider, messages, tools):
            previous = messages[-4]["tool_calls"]
            results = messages[-3:]
            self.assertEqual([m["role"] for m in results], ["tool"] * 3)
            self.assertEqual([m["tool_call_id"] for m in results], [c["id"] for c in previous])
            payloads = tool_results(messages)
            self.assertEqual(payloads[1]["狀態"], "rejected")
            self.assertEqual(payloads[1]["錯誤代碼"], "TERMINAL_MUST_BE_ALONE")
            return response(
                supported("霧潮貼圖多少錢？", "20 枚", (f"{CATALOG.current}/shop-items/mist-tide-sticker", "")),
                supported("上期星航相框多少錢？", "25 枚", (f"{CATALOG.previous}/shop-items/star-voyage-frame", "")))
        text, trace, events, _ = self.run_case(q,
            select(item("霧潮貼圖"), call(FINAL_TOOL, results=[abstain(q)]),
                   item("星航相框", "previous")), finish)
        self.assertEqual([t["工具"] for t in trace], ["find_shop_item", "find_shop_item"])
        self.assertIn("20 枚", text)
        self.assertIn("25 枚", text)
        self.assertEqual([d["工具嘗試"] for t, d in events if t == "Python 單一 Agent 輪次"], [0, 2])

    def test_mixed_batch_keeps_invalid_data_call_rejected(self):
        q = "查 B 玩家餘額，霧潮貼圖多少錢？"
        def finish(provider, messages, tools):
            results = tool_results(messages)
            self.assertEqual([p["狀態"] for p in results], ["rejected", "invalid_arguments", "found"])
            return response(abstain("查 B 玩家餘額", "account_limit"),
                            supported("霧潮貼圖多少錢？", "20 枚", (f"{CATALOG.current}/shop-items/mist-tide-sticker", "")))
        text, trace, _, _ = self.run_case(q, select(call(FINAL_TOOL, results=[abstain(q)]),
            call("get_my_event_progress", edition="current", account_scope="other"), item("霧潮貼圖")), finish)
        self.assertEqual([t["工具"] for t in trace], ["find_shop_item"])
        self.assertIn("20 枚", text)

    def test_multiple_terminal_calls_are_bounded(self):
        q = "今天台北天氣如何？"
        batch = select(call(FINAL_TOOL, results=[abstain(q, "service_help")]),
                       call(FINAL_TOOL, results=[abstain(q, "service_help")]))
        def finish(provider, messages, tools, *, response_schema):
            self.assertEqual(tools, [])
            self.assertEqual(len(tool_results(messages)), MAX_TOOL_ROUNDS * 2)
            self.assertTrue(all(p["狀態"] == "rejected" for p in tool_results(messages)))
            return structured(abstain(q, "service_help"))
        text, trace, events, _ = self.run_case(q, *([batch] * MAX_TOOL_ROUNDS), finish)
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["service_help"])
        self.assertTrue(all(d["工具嘗試"] == 0 for t, d in events if t == "Python 單一 Agent 輪次"))

    def test_rag_metrics_record_public_rank_without_injecting_new_messages(self):
        q = "週末巡燈規則是什麼？"
        text, trace, events, _ = self.run_case(q,
            select(call("find_rules", edition="current", query=q, mode="search")),
            response(abstain(q)))
        self.assertEqual([t["工具"] for t in trace], ["find_rules"])
        metrics = [d for t, d in events if t == "Python RAG 檢索指標"]
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["結果數"], len(metrics[0]["候選"]))
        self.assertTrue(any("BM25" in row for row in metrics[0]["候選"]))
        self.assertTrue(all("原文" not in row for row in metrics[0]["候選"]))
        self.assertTrue(any(t == "Python 工具執行指標" for t, _ in events))

    def test_invalid_terminal_schema_uses_constrained_fallback(self):
        q = "幫我修改餘額"
        def finish(provider, messages, tools, *, response_schema):
            self.assertEqual(tools, [])
            self.assertEqual(messages[-1]["role"], "tool")
            self.assertEqual(messages[-1]["tool_call_id"], messages[-2]["tool_calls"][0]["id"])
            self.assertEqual(tool_results(messages)[-1]["錯誤代碼"], "INVALID_FINAL_SCHEMA")
            return structured(abstain(q, "unsupported"))
        text, trace, _, _ = self.run_case(q,
            select(call(FINAL_TOOL, results=[{"question": q, "status": "wrong"}])),
            finish)
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["unsupported"])

    def test_verified_absence_recovery_uses_only_trusted_lookup(self):
        q = "不存在的紀念徽章要多少星星幣？"
        def finish(provider, messages, tools):
            payload = tool_results(messages)[-1]
            self.assertEqual(payload["狀態"], "not_found")
            return response({"question": q, "status": "no_evidence", "answer": "可能需要 100 枚",
                             "source_ids": [payload["段落"][0]["段落ID"]]})
        text, trace, _, _ = self.run_case(q, select(item("不存在的紀念徽章")), finish)
        self.assertIn("查無", text)
        self.assertNotIn("100 枚", text)
        self.assertEqual(len(trace), 1)

    def test_inconsistent_status_is_repaired_before_terminal_acceptance(self):
        q = "霧潮貼圖是免費的嗎？"
        key = f"{CATALOG.current}/shop-items/mist-tide-sticker"
        good = supported(q, "不是免費，需 20 枚星星幣。", (key, ""))
        bad = {**good, "status": "no_evidence"}
        def fix(provider, messages, tools, *, response_schema):
            self.assertEqual(tools, [])
            feedback = tool_results(messages)[-1]
            self.assertEqual(feedback["錯誤代碼"], "INVALID_FINAL_EVIDENCE")
            self.assertIn("非 supported", feedback["原因"])
            self.assertIn("肯定或否定", messages[0]["content"])
            return structured(good)
        text, trace, events, _ = self.run_case(q, select(item("霧潮貼圖")), response(bad), fix)
        self.assertIn("20 枚", text)
        self.assertEqual(len(trace), 1)
        self.assertFalse(any(t == "Python 接受終止工具" for t, _ in events))

    def test_mixed_found_and_not_found_is_repaired_without_relaxing_guardrail(self):
        q = "霧潮貼圖多少錢？"
        def invalid(provider, messages, tools):
            rows = [p for r in tool_results(messages) for p in r["段落"]]
            return response({"question": q, "status": "supported", "answer": "20 枚",
                             "source_ids": [p["段落ID"] for p in rows]})
        def fix(provider, messages, tools, *, response_schema):
            feedback = tool_results(messages)[-1]
            self.assertEqual(feedback["錯誤代碼"], "INVALID_FINAL_EVIDENCE")
            self.assertIn("檔期", feedback["原因"])
            return structured(supported(q, "20 枚", (f"{CATALOG.current}/shop-items/mist-tide-sticker", "")))
        text, trace, _, _ = self.run_case(q,
            select(item("霧潮貼圖"), item("霧潮貼圖", "previous")), invalid, fix)
        self.assertIn("20 枚", text)
        self.assertEqual(len(trace), 2)

    def test_schema_fallback_bad_status_does_not_promote_answer(self):
        q = "霧潮貼圖多少錢？"
        bad = {**supported(q, "20 枚", (f"{CATALOG.current}/shop-items/mist-tide-sticker", "")),
               "status": "no_evidence"}
        text, trace, _, _ = self.run_case(q, select(item("霧潮貼圖")),
            {"content": "20 枚"}, structured(bad))
        self.assertIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertNotIn("20 枚", text)
        self.assertEqual(len(trace), 1)

    def test_exhausted_repair_preserves_valid_independent_part(self):
        q = "霧潮貼圖多少錢？我的餘額是多少？"
        good = supported("霧潮貼圖多少錢？", "20 枚", (f"{CATALOG.current}/shop-items/mist-tide-sticker", ""))
        bad = {"question": "我的餘額是多少？", "status": "supported", "answer": "999",
               "source_ids": ["E000000000000"]}
        text, trace, _, _ = self.run_case(q, select(item("霧潮貼圖")),
            response(good, bad), {"content": "invalid json"})
        self.assertIn("20 枚", text)
        self.assertIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertNotIn("999", text)
        self.assertEqual(len(trace), 1)



    def test_evidence_scope_guidance_is_shared_with_schema_fallback(self):
        q = "活動規則有哪些？還有公告沒有說明的資訊呢？"
        def native(provider, messages, tools):
            system = messages[0]["content"]
            self.assertIn("未公告、未提及或搜尋未找到，不等於事實不存在", system)
            self.assertIn("符合部分資格不代表符合全部資格", system)
            self.assertIn("無關檔期", system)
            self.assertIn("主機若指定僅 Schema 回答", system)
            definitions = {t["function"]["name"]: t["function"] for t in tools}
            for name, guidance in (
                ("find_rules", "不證明政策不存在"),
                ("find_shop_item", "欄位未提供"),
                ("list_shop_items", "不提供個人交易結果"),
                ("get_my_event_progress", "實際回傳的欄位"),
                ("calculate", "不證明全部業務資格"),
                (FINAL_TOOL, "未知需求用 no_evidence"),
            ):
                self.assertIn(guidance, definitions[name]["description"])
            self.assertEqual(definitions[FINAL_TOOL]["parameters"], FINAL_SCHEMA)
            return {"content": "未提交終止工具"}
        def fallback(provider, messages, tools, *, response_schema):
            self.assertEqual(tools, [])
            self.assertEqual(response_schema, FINAL_SCHEMA)
            self.assertIn("未公告或未查到不等於不存在", messages[0]["content"])
            self.assertIn("正文及來源留空", messages[0]["content"])
            return structured(abstain(q, "no_evidence"))
        text, trace, _, _ = self.run_case(q, native, fallback)
        self.assertEqual(text, DIRECT_REPLIES["no_evidence"])
        self.assertEqual(trace, [])

    def test_supported_and_unknown_independent_needs_remain_separate(self):
        q = "霧潮貼圖多少錢？如果不喜歡能退幣嗎？"
        price_q = "霧潮貼圖多少錢？"
        unknown_q = "如果不喜歡能退幣嗎？"
        def finish(provider, messages, tools):
            return response(
                supported(price_q, "20 枚星星幣", (f"{CATALOG.current}/shop-items/mist-tide-sticker", "")),
                abstain(unknown_q, "no_evidence"))
        text, trace, events, _ = self.run_case(q, select(item("霧潮貼圖")), finish)
        self.assertIn("20 枚星星幣", text)
        self.assertIn(unknown_q, text)
        self.assertIn(DIRECT_REPLIES["no_evidence"], text)
        self.assertEqual(len(trace), 1)
        checks = [d for t, d in events if t == "Python 檢查回答依據"]
        parts = checks[-1]["子問題"]
        self.assertEqual([p["判定"] for p in parts], ["supported", "no_evidence"])
        self.assertEqual(parts[1]["採用段落"], [])



    def test_unknown_progress_explains_scope_without_disclosing_values(self):
        q = "本期共玩了幾天？"
        with patch("guide.read_progress", return_value=(CATALOG.as_of, 987654, 2, True)):
            text, trace, events, _ = self.run_case(q,
                select(call("get_my_event_progress", edition="current", account_scope="self")),
                response(abstain(q)))
        self.assertIn("不包含累計遊玩天數", text)
        self.assertNotIn("987654", text)
        self.assertEqual(len(trace), 1)
        limit = [d for t, d in events if t == "Python 呈現資料限制"][-1]
        self.assertEqual(limit["狀態"], ["progress_scope"])

    def test_unknown_requirements_share_one_limitation_without_losing_labels(self):
        q = "可以退款嗎？可以轉贈嗎？"
        first, second = "可以退款嗎？", "可以轉贈嗎？"
        text, trace, events, _ = self.run_case(q,
            select(call("find_rules", edition="current", mode="search", query="退款 轉贈")),
            response(abstain(first), abstain(second)))
        self.assertIn(first, text)
        self.assertIn(second, text)
        self.assertEqual(text.count(DIRECT_REPLIES["no_evidence"]), 1)
        self.assertEqual(text.count("本輪查詢情況："), 1)
        self.assertEqual(len(trace), 1)
        checks = [d for t, d in events if t == "Python 檢查回答依據"][-1]
        self.assertEqual([p["判定"] for p in checks["子問題"]], ["no_evidence", "no_evidence"])
        self.assertEqual(checks["參考資料"], [])

    def test_tool_failure_is_not_absence_and_does_not_leak_error(self):
        q = "目前活動有哪些規則？"
        with patch.object(ToolRegistry, "execute", side_effect=RuntimeError("SECRET_DSN")):
            text, trace, events, _ = self.run_case(q,
                select(call("find_rules", edition="current", mode="overview", query="")),
                response(abstain(q)))
        self.assertEqual(trace, [])
        self.assertIn("未能完成查詢", text)
        self.assertNotIn("SECRET_DSN", text)
        self.assertNotIn("已取得活動公告資料", text)
        self.assertEqual([d for t, d in events if t == "Python 呈現資料限制"][-1]["狀態"], ["tool_error"])

    def test_limitations_never_appear_on_account_boundary_only(self):
        q = "幫我查朋友 B 的餘額"
        text, trace, events, _ = self.run_case(q,
            select(call("get_my_event_progress", edition="current", account_scope="other")),
            response(abstain(q, "account_limit")))
        self.assertEqual(text, DIRECT_REPLIES["account_limit"])
        self.assertEqual(trace, [])
        self.assertNotIn("Python 呈現資料限制", [t for t, _ in events])

    def test_previous_turn_limitations_are_not_reused(self):
        q = "本期共玩了幾天？"
        with patch("guide.read_progress", return_value=(CATALOG.as_of, 80, 2, True)):
            text, trace, _, _ = self.run_case(q,
                select(call("get_my_event_progress", edition="current", account_scope="self")),
                response(abstain(q)))
        self.assertIn("活動進度查詢提供", text)
        followup = "那其他規則呢？"
        history = [{"role": "user", "content": q},
                   {"role": "assistant", "content": text, "trace": trace}]
        model = scripted(response(abstain(followup)))
        events = []
        reply, current_trace = asyncio.run(answer("A", "ollama", history, followup, model,
            lambda title, data: events.append((title, data))))
        self.assertEqual(reply, DIRECT_REPLIES["no_evidence"])
        self.assertEqual(current_trace, [])
        self.assertEqual([d for t, d in events if t == "Python 呈現資料限制"][-1]["狀態"],
                         ["no_observed_data_context"])

    def test_missing_progress_row_preserves_unavailable_provenance(self):
        edition = CATALOG.current
        raw = {"檔期": edition, "狀態": "沒有這個檔期的玩家進度紀錄"}
        source, _, _ = evidence_for("get_my_event_progress", {"edition": edition}, raw, "我的餘額？")
        row = source["段落"][0]
        self.assertEqual(source["狀態"], "unavailable")
        self.assertEqual(row["資料狀態"], "unavailable")
        identifier = row["段落ID"]
        part = {"question": "我的餘額？", "status": "supported", "answer": "999 枚",
                "citations": [{"id": identifier, "quote": row["原文"]}]}
        text, citations = validate_part(part, {identifier: row})
        self.assertEqual(text, row["原文"])
        self.assertNotIn("999", text)
        self.assertEqual(citations, part["citations"])
        other = {"原文": "公開玩法規則"}
        mixed = {**part, "citations": [*part["citations"], {"id": "rule", "quote": other["原文"]}]}
        with self.assertRaises(ValueError):
            validate_part(mixed, {identifier: row, "rule": other})

    def test_unavailable_progress_no_evidence_is_not_zero_balance(self):
        q = "我的餘額有多少？"
        with patch("guide.read_progress", return_value=None):
            text, trace, events, _ = self.run_case(q,
                select(call("get_my_event_progress", edition="current", account_scope="self")),
                response(abstain(q)))
        self.assertIn("未取得紀錄", text)
        self.assertIn("不能據此推論餘額", text)
        self.assertEqual(len(trace), 1)
        checks = [d for t, d in events if t == "Python 檢查回答依據"][-1]
        self.assertEqual(checks["子問題"][0]["判定"], "no_evidence")



    def test_repeated_rule_results_are_feedback_not_forced_semantic_stop(self):
        from guide import published_passages
        q = "活動還有哪些需要確認的規則？"
        rows = published_passages(CATALOG.current)
        first, second = rows[0], rows[1]
        queries = [call("find_rules", edition="current", mode="search", query=x)
                   for x in ("第一種查詢", "第二種查詢", "第三種查詢")]
        def second_query(provider, messages, tools):
            self.assertNotIn("主機觀測：上一批成功公告查詢沒有新增", messages[0]["content"])
            return select(queries[1])
        def third_query(provider, messages, tools):
            self.assertIn("主機觀測：上一批成功公告查詢沒有新增", messages[0]["content"])
            self.assertTrue(tools)
            return select(queries[2])
        def finish(provider, messages, tools, *, response_schema):
            self.assertEqual(tools, [])
            self.assertEqual(response_schema, FINAL_SCHEMA)
            return structured(abstain(q))
        with patch("backend.search_rules", side_effect=[[first], [first], [first, second]]):
            text, trace, events, _ = self.run_case(q, select(queries[0]), second_query, third_query, finish)
        self.assertEqual(len(trace), 3)
        progress = [d for title, d in events if title == "Python 查詢進展"]
        self.assertEqual([p["new_rule_ids"] for p in progress], [1, 0, 1])
        self.assertEqual([p["already_known_rule_ids"] for p in progress], [0, 1, 1])
        rounds = [d for title, d in events if title == "Python 單一 Agent 輪次"]
        self.assertEqual(rounds[-1]["停止資料工具原因"], ["tool_round_limit"])
        self.assertEqual([d for title, d in events if title == "Python 檢索快取統計"][-1]["version"], 1)

    def test_terminal_repair_stop_reason_is_observable(self):
        q = "其他帳號可以代查嗎？"
        def finish(provider, messages, tools, *, response_schema):
            self.assertEqual(tools, [])
            self.assertEqual(response_schema, FINAL_SCHEMA)
            return structured(abstain(q, "account_limit"))
        text, trace, events, _ = self.run_case(q, {"content": "未提交終止工具"}, finish)
        self.assertEqual(text, DIRECT_REPLIES["account_limit"])
        self.assertEqual(trace, [])
        rounds = [d for title, d in events if title == "Python 單一 Agent 輪次"]
        self.assertEqual(rounds[-1]["停止資料工具原因"], ["terminal_repair"])
        self.assertEqual([d["錯誤代碼"] for title, d in events
                          if title == "Python 單一 Agent 回傳驗證失敗"], ["INVALID_FINAL_SCHEMA"])
