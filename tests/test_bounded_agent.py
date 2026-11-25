"""Offline end-to-end contracts: real local tools, scripted model responses."""
import asyncio
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import AIMessage
from pathlib import Path
from unittest.mock import Mock, patch

from agent import MAX_TOOL_CALLS, MAX_TOOL_ROUNDS, legacy_answer as answer
from answer_quality import REVIEW_SCHEMA
from answering import ANSWER_SCHEMA, ANSWER_VERIFICATION_FAILED, DIRECT_REPLIES, UNRESOLVED
from backend import ToolRegistry, calculate
from guide import CATALOG, progress, published_passages, shop_catalog
from evidence import source_alias
from model_client import completion
from request_context import RequestContext
from tests.test_guide import OfflineTests

CURRENT = CATALOG.current
PREVIOUS = CATALOG.previous


def call(tool_name, **args):
    return {"id": "provider-call", "type": "function",
            "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)}}


def select(*calls):
    return {"role": "assistant", "content": "", "tool_calls": list(calls)}


def rules(query="", mode="search", edition="current"):
    # Retrieval fixtures retain the historical eligibility label; live tools expose search.
    return call("find_rules", edition=edition, query=query, mode="search" if mode == "eligibility" else mode)


def response(*parts, action="answer", missing=""):
    return {"content": json.dumps({"action": action, "missing": missing, "results": list(parts)}, ensure_ascii=False)}


def fixture_id(key):
    # Fixtures may refer to a canonical ID or a short ID read from the actual packet.
    import re
    return key if re.fullmatch(r"E[0-9a-f]{12}", key) else source_alias(key)


def supported(question, text, *citations):
    return {"question": question, "status": "supported", "answer": text,
            "source_ids": [fixture_id(key) for key, _ in citations]}


def abstain(question, status="no_evidence"):
    return {"question": question, "status": status, "answer": "", "source_ids": []}


def original(key):
    return next(row["原文"] for edition in CATALOG.editions for row in published_passages(edition)
                if row["段落ID"] == key)


def packet(messages):
    return json.loads(messages[-1]["content"])


def evidence_rows(messages):
    return {row["段落ID"]: row for src in packet(messages)["工具結果"] for row in src["段落"]}


def scripted(*replies):
    """Invoke callback replies with actual model arguments; never leak StopIteration."""
    queue = iter(replies)
    def respond(*args, **kwargs):
        try:
            step = next(queue)
        except StopIteration as error:
            raise AssertionError("Unexpected extra model call") from error
        return step(*args, **kwargs) if callable(step) else step
    return Mock(side_effect=respond)


class BoundedAgentTests(OfflineTests):
    def setUp(self):
        super().setUp()
        config = patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_ANSWER_REVIEW": "off"})
        config.start()
        self.addCleanup(config.stop)

    def run_case(self, question, *replies, player="A", history=()):
        model = scripted(*replies)
        events = []
        text, trace = asyncio.run(answer(player, "ollama", list(history), question, model,
                                         lambda title, data: events.append((title, data))))
        self.assertEqual(model.call_count, len(replies))
        for invocation in model.call_args_list:
            tools = invocation.args[2]
            if tools:
                self.assertNotIn("response_schema", invocation.kwargs)
                self.assertNotIn("submit_answer", [t["function"]["name"] for t in tools])
            else:
                self.assertEqual(invocation.kwargs.get("response_schema"), ANSWER_SCHEMA)
        return text, trace, events, model

    def test_overview_two_model_calls_and_complete_sources(self):
        q = "我剛來，活動怎麼玩？"
        rows = [p for p in published_passages(CURRENT) if p["文件ID"] == "launch"]
        text, trace, events, model = self.run_case(q, select(rules(mode="overview")),
            response(supported(q, "\n".join(p["原文"] for p in rows),
                               *((p["段落ID"], p["原文"]) for p in rows))))
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["參數"]["mode"], "overview")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(p["原文"] in text for p in rows))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(len(evidence_rows(model.call_args.args[1])), 3)

    def test_empty_native_selection_recovers_using_same_registry_contract(self):
        q = "這個活動要怎麼玩?"
        rows = [p for p in published_passages(CURRENT) if p["文件ID"] == "launch"]
        text, trace, events, model = self.run_case(q, {"role": "assistant", "content": ""},
            select(rules(mode="overview")),
            response(supported(q, "\n".join(p["原文"] for p in rows),
                               *((p["段落ID"], p["原文"]) for p in rows))))
        self.assertEqual([call["參數"]["mode"] for call in trace], ["overview"])
        self.assertTrue(all(p["原文"] in text for p in rows))
        self.assertEqual(model.call_count, 3)
        self.assertIn("送出模型請求 · 重試資料工具", [title for title, _ in events])

    def test_both_providers_ignore_text_tool_requests_and_retry_natively(self):
        question = "查詢目前可兌換的商品"
        fake = {"content": json.dumps({"calls": [{"name": "list_shop_items",
                                                 "arguments": {"edition": "current"}}]})}
        for provider in ("ollama", "openai"):
            with self.subTest(provider=provider), patch.dict(os.environ, {
                    "EVENT_SUPPORT_AGENT_TOOL_SELECTION": "structured"}):
                model = scripted(fake, response(abstain(question)), select(), response(abstain(question)))
                text, trace = asyncio.run(answer("A", provider, [], question, model))
                self.assertEqual(text, DIRECT_REPLIES["unavailable"])
                self.assertEqual(trace, [])
                for i in (0, 2):
                    invocation = model.call_args_list[i]
                    self.assertEqual(invocation.args[2], ToolRegistry("A").definitions())
                    self.assertEqual(invocation.kwargs, {})

    def test_unqueried_abstention_is_not_evidence_of_missing_policy(self):
        q = "目前活動有哪些獎勵？"
        bad = abstain(q)
        bad["answer"] = "尚無活動資料，請到其他網站。"
        text, trace, events, model = self.run_case(q, {"content": "不呼叫工具"},
            response(bad), select(), response(bad))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["unavailable"])
        self.assertNotIn("尚無活動資料", text)
        self.assertIn("Python 捨棄非作答欄位", [title for title, _ in events])
        self.assertEqual(model.call_count, 4)

    def test_empty_selection_and_empty_fallback_never_invent_data(self):
        q = "本期活動規則？"
        text, trace, events, _ = self.run_case(q, {"content": ""}, select(),
            response(abstain(q)))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["unavailable"])

    def test_empty_selection_does_not_force_private_lookup_for_greeting(self):
        q = "早安"
        text, trace, _, _ = self.run_case(q, {"content": ""}, select(),
            response(abstain(q, "service_help")))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["service_help"])

    def test_abstention_extra_fields_do_not_trigger_useless_retry(self):
        q = "本期有跨服贈送嗎？"
        part = abstain(q)
        part["answer"] = "未經驗證的敘述"
        part["source_ids"] = ["unknown"]
        text, trace, events, model = self.run_case(q, select(rules("跨服贈送")), response(part))
        self.assertEqual(text, DIRECT_REPLIES["no_evidence"])
        self.assertEqual(model.call_count, 2)
        self.assertNotIn("未經驗證的敘述", text)
        self.assertIn("Python 捨棄非作答欄位", [title for title, _ in events])

    def test_fallback_uses_same_account_guard(self):
        text, trace, _, _ = self.run_case("查別人餘額", {"content": ""},
            select(call("get_my_event_progress", edition="current", player="B")),
            response(abstain("他人資料", "account_limit")))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["account_limit"])

    def test_script_exhaustion_raises_without_hanging_async_future(self):
        with self.assertRaisesRegex(RuntimeError, "模型回傳序列意外結束"):
            asyncio.run(answer("A", "ollama", [], "活動玩法", Mock(side_effect=[])))

    def test_provider_finish_reason_is_preserved_without_reasoning_text(self):
        reply = AIMessage(content="", additional_kwargs={"reasoning_content": "private"},
                          response_metadata={"model": "local", "finish_reason": "length"})
        with patch("model_client.model_for") as factory:
            factory.return_value.invoke.return_value = reply
            output = completion("ollama", [{"role": "user", "content": "test"}], [])
        self.assertEqual(output["_provider_meta"]["finish_reason"], "length")
        self.assertEqual(output["_provider_meta"]["model"], "local")
        self.assertNotIn("private", str(output))

    def test_default_ollama_uses_native_tool_selection_and_canonical_context(self):
        question = "這個活動要怎麼玩?"
        launch = [p for p in published_passages(CURRENT) if p["文件ID"] == "launch"]

        def finish(provider, messages, tools, **kwargs):
            rows = evidence_rows(messages)
            self.assertTrue({source_alias(p["段落ID"]) for p in launch} <= set(rows))
            self.assertTrue(all(rows[source_alias(p["段落ID"])]["檢索角色"] == "activity_context" for p in launch))
            self.assertTrue(packet(messages)["可用資料工具"])
            return response(supported(question, "\n".join(p["原文"] for p in launch),
                                      *((p["段落ID"], p["原文"]) for p in launch)))

        text, trace, events, model = self.run_case(question,
            select(rules("参与方式", "search")), finish)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args_list[0].args[2], ToolRegistry("A").definitions())
        self.assertEqual(trace[0]["參數"]["mode"], "search")
        self.assertTrue(all(p["原文"] in text for p in launch))
        self.assertNotIn("送出模型請求 · 重試資料工具", [title for title, _ in events])

    def test_native_supplement_receives_actual_missing_data_reason(self):
        question = "我可以換什麼？還想知道活動期限"
        def select_again(provider, messages, tools, **kwargs):
            payload = packet(messages)
            self.assertEqual(payload["系統補查回饋"], "還需要商城清單")
            self.assertTrue(any(m["role"] == "tool" for m in messages))
            return select(call("list_shop_items", edition="current"))
        item = shop_catalog()["items"][0]
        text, trace, _, _ = self.run_case(question, select(rules(mode="overview")),
            response(action="retrieve", missing="還需要商城清單"),
            select_again, response(supported("商品", item["name"],
               (f"{CURRENT}/shop-items/{item['id']}", item["name"]))))
        self.assertEqual([entry["工具"] for entry in trace], ["find_rules", "list_shop_items"])

    def test_context_does_not_imply_specific_policy_exists(self):
        from backend import evidence_for
        registry = ToolRegistry("A")
        q = "ZXQ-9967"
        ctx = RequestContext.build(q, [])
        args = registry.validate("find_rules", {"edition": "previous", "query": q, "mode": "search"}, ctx, {})
        rows = registry.execute("find_rules", args)
        self.assertTrue(rows)
        self.assertTrue(all(p["檔期"] == PREVIOUS and p["文件ID"] == "launch" for p in rows))
        source, _, _ = evidence_for("find_rules", args, rows, q)
        self.assertEqual(source["狀態"], "context_only")
        text, _, _, _ = self.run_case(q, select(rules(q, edition="previous")), response(abstain(q)))
        self.assertEqual(text, DIRECT_REPLIES["no_evidence"])

    def test_duplicate_question_is_rendered_once_with_verified_partial_answer(self):
        q = "這個活動要怎麼玩?"
        key = f"{CURRENT}/launch/玩法"
        text, trace, events, _ = self.run_case(q, select(rules("参与方式")),
            response(abstain(q), supported(q, "每天前三場正式冒險可取得活動幣。",
                                            (key, "每天前 3 場冒險各得 20 枚星星幣"))))
        self.assertIn("每天前三場正式冒險", text)
        self.assertIn("仍有部分內容缺乏足夠依據", text)
        self.assertNotIn(DIRECT_REPLIES["no_evidence"], text)
        self.assertLessEqual(text.count(q), 1)
        self.assertEqual(dict(events)["Python 檢查回答依據"]["判定"], "partial")

    def test_search_can_expand_to_overview_after_evidence_assessment(self):
        q = "這個活動要怎麼玩?"
        rows = [p for p in published_passages(CURRENT) if p["文件ID"] == "launch"]
        text, trace, events, _ = self.run_case(q, select(rules("玩法")),
            response(action="retrieve", missing="缺少參加與兌換的完整流程"),
            select(rules(mode="overview")),
            response(supported(q, "\n".join(p["原文"] for p in rows),
                               *((p["段落ID"], p["原文"]) for p in rows))))
        self.assertEqual([t["參數"]["mode"] for t in trace], ["search", "overview"])
        self.assertTrue(all(p["原文"] in text for p in rows))
        self.assertIn("Python 接受補查需求", [title for title, _ in events])

    def test_public_catalogue_followup_rejects_joined_names_then_recovers(self):
        q = "那其他價格多少?"
        overview_rows = [p for p in published_passages(CURRENT) if p["文件ID"] == "launch"]
        prior, prior_trace, _, _ = self.run_case("活動怎麼玩？", select(rules(mode="overview")),
            response(supported("活動玩法", "\n".join(p["原文"] for p in overview_rows),
                               *((p["段落ID"], p["原文"]) for p in overview_rows))))
        history = [{"role": "user", "content": "活動怎麼玩？"},
                   {"role": "assistant", "content": prior, "trace": prior_trace}]
        items = shop_catalog()["items"][1:]
        citations = [(f"{CURRENT}/shop-items/{i['id']}", f"「{i['name']}」需要 {i['price']} 枚星星幣。")
                     for i in items]
        text, trace, events, _ = self.run_case(q,
            select(call("find_shop_item", edition="context", name="、".join(i["name"] for i in items))),
            response(action="retrieve", missing="需要公開商城清單及各品項價格"),
            select(call("list_shop_items", edition="context")),
            response(supported(q, "\n".join(quote for _, quote in citations), *citations)), history=history)
        self.assertEqual([t["工具"] for t in trace], ["list_shop_items"])
        self.assertTrue(all(quote in text for _, quote in citations))
        self.assertIn("Python 工具驗證失敗", [title for title, _ in events])

    def test_trusted_product_reference_and_cross_period_rejection(self):
        item = shop_catalog()["items"][0]
        q = "那個要多少？"
        history = [{"role": "user", "content": "披風是什麼？"},
                   {"role": "assistant", "content": "披風外觀", "trace": [
                       {"工具": "find_shop_item", "參數": {"edition": CURRENT, "name": "披風"},
                        "商品參照": [item["id"]]}]}]
        text, trace, _, _ = self.run_case(q, select(call("find_shop_item", edition="context", name=item["name"])),
            response(supported(q, "100 枚星星幣", (f"{CURRENT}/shop-items/{item['id']}", "100 枚星星幣"))),
            history=history)
        self.assertEqual(trace[0]["參數"]["name"], item["name"])
        self.assertIn("100 枚星星幣", text)
        ctx = RequestContext.build(q, history)
        self.assertFalse(ctx.grounded_product(item["name"], PREVIOUS))
        self.assertFalse(ctx.grounded_product("不存在的商品", CURRENT))

    def test_two_products_are_independent_tool_calls(self):
        items = shop_catalog()["items"][1:3]
        q = "霧潮貼圖和海圖航線框各要多少？"
        text, trace, _, _ = self.run_case(q,
            select(*(call("find_shop_item", edition="current", name=i["name"]) for i in items)),
            response(*(supported(i["name"], f"{i['price']} 枚星星幣",
                                 (f"{CURRENT}/shop-items/{i['id']}", f"{i['price']} 枚星星幣")) for i in items)))
        self.assertEqual(len(trace), 2)
        self.assertTrue(all(f"{i['price']} 枚星星幣" in text for i in items))

    def test_unknown_product_status_and_independent_rule(self):
        q = "七龍珠模型換不到，另外 SG-204 是什麼意思？"
        key = f"{CURRENT}/shop/訂單處理中"

        def finish(provider, messages, tools, **kwargs):
            rows = evidence_rows(messages)
            missing = next(row for row in rows.values() if row.get("資料狀態") == "not_found")
            return response(supported("指定商品", "不可相信這句；應顯示真實查詢狀態",
                                      (missing["段落ID"], missing["原文"])),
                            supported("錯誤碼的一般意思", "請查兌換紀錄", (key, original(key))))

        text, trace, _, _ = self.run_case(q, select(
            call("find_shop_item", edition="current", name="七龍珠模型"), rules("SG-204")), finish)
        self.assertEqual([t["工具"] for t in trace], ["find_shop_item", "find_rules"])
        self.assertIn("商城清單查無「七龍珠模型」", text)
        self.assertNotIn("不可相信這句", text)
        self.assertIn("兌換紀錄", text)
        self.assertNotIn("list_shop_items", [t["工具"] for t in trace])

    def test_invalid_citation_does_not_discard_valid_partial_answer(self):
        q = "我的星星幣有多少？披風能跨服送嗎？"
        text, trace, events, _ = self.run_case(q,
            select(call("get_my_event_progress", edition="current"), rules("跨服贈送")),
            response(supported("目前餘額", "80 枚星星幣", (f"{CURRENT}/my-progress", '"星星幣": 80')),
                     supported("跨服贈送", "可以免費轉送", ("invented", "免費")),
                     abstain("手續費")))
        self.assertIn("80 枚星星幣", text)
        self.assertIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertIn(DIRECT_REPLIES["no_evidence"], text)
        self.assertNotIn("可以免費轉送", text)
        checks = dict(events)["Python 檢查回答依據"]["子問題"]
        self.assertEqual([c["判定"] for c in checks], ["supported", "rejected", "no_evidence"])

    def test_restricted_rule_must_match_current_user_facts(self):
        key = f"{CURRENT}/shop/訂單處理中"
        for q in ("這筆是 SG-2049，怎麼辦？", "兌換出錯了"):
            with self.subTest(question=q):
                text, trace, events, _ = self.run_case(q, select(rules("SG-204")),
                    response(supported(q, "依 SG-204 操作", (key, original(key)))))
                self.assertIn(ANSWER_VERIFICATION_FAILED, text)
                self.assertIn("Python 核對公告適用條件", [title for title, _ in events])
                self.assertNotIn("依 SG-204 操作", text)

    def test_cross_period_evidence_and_unavailable_edition(self):
        old_id, new_id = f"{PREVIOUS}/launch/玩法", f"{CURRENT}/launch/玩法"
        q = "十月與十一月的排位規則有何不同？"
        text, trace, _, _ = self.run_case(q, select(rules("排位", edition="previous"), rules("排位")),
            response(supported(q, "十月可計排位，十一月排位不計",
                               (old_id, "冒險或排位"), (new_id, "排位不計"))))
        self.assertEqual([t["參數"]["edition"] for t in trace], [PREVIOUS, CURRENT])
        self.assertIn("十月可計排位", text)
        text, trace, _, _ = self.run_case("去年活動呢？", select(rules(mode="overview", edition="unavailable")),
            response(abstain("去年活動", "unavailable")))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["unavailable"])

    def test_account_identity_cannot_be_selected_by_model(self):
        ctx = RequestContext.build("我的餘額", [])
        with ThreadPoolExecutor(max_workers=3) as pool:
            def read(player):
                registry = ToolRegistry(player)
                args = registry.validate("get_my_event_progress", {"edition": "current"}, ctx, {})
                return registry.execute("get_my_event_progress", args)
            records = list(pool.map(read, "ABC" * 3))
        self.assertEqual([r["星星幣"] for r in records], [80, 80, 0] * 3)
        self.assertEqual([r["今日已計入場次"] for r in records], [2, 3, 0] * 3)
        text, trace, events, _ = self.run_case("我要查 B 的餘額",
            select(call("get_my_event_progress", edition="current", player="B")),
            response(abstain("他人餘額", "account_limit")))
        self.assertEqual(trace, [])
        self.assertIn(DIRECT_REPLIES["account_limit"], text)
        self.assertIn("Python 工具驗證失敗", [title for title, _ in events])

    def test_eligibility_calculation_and_distinct_daily_limits(self):
        price = shop_catalog()["items"][0]
        price_id = f"{CURRENT}/shop-items/{price['id']}"
        rule_id, record_id = f"{CURRENT}/launch/玩法", f"{CURRENT}/my-progress"
        q = "再打一場冒險能達到披風價格門檻嗎？"
        for player, expression, total in (("A", "80+20", 100), ("B", "80+0", 80)):
            with self.subTest(player=player):
                def finish(provider, messages, tools, **kwargs):
                    rows = evidence_rows(messages)
                    calc = next(row for row in rows.values() if row.get("運算依據"))
                    return response(supported(q, f"依今天已計場次，預估餘額 {total} 枚；僅判斷價格門檻。",
                        (price_id, "100 枚星星幣"), (rule_id, "每天前 3 場冒險各得 20 枚星星幣"),
                        (record_id, f'"今日已計入場次": {progress(player)["今日已計入場次"]}'),
                        (calc["段落ID"], f"{expression} = {total}")))
                text, trace, _, _ = self.run_case(q, select(
                    call("find_shop_item", edition="current", name="披風"),
                    rules("冒險 星星幣", "eligibility"), call("get_my_event_progress", edition="current")),
                    response(action="retrieve", missing="需要計算預估餘額"),
                    select(call("calculate", expression=expression, source_ids=[price_id, rule_id, record_id])),
                    finish, player=player)
                self.assertEqual(len(trace), 4)
                self.assertIn(f"預估餘額 {total} 枚", text)
                self.assertEqual(json.loads(trace[-1]["結果"])["result"], str(total))

    def test_missing_record_field_is_not_guessed(self):
        q = "本期總共玩了幾天？"
        text, trace, _, model = self.run_case(q, select(call("get_my_event_progress", edition="current")),
            response(abstain(q)))
        record = json.loads(evidence_rows(model.call_args.args[1])[source_alias(f"{CURRENT}/my-progress")]["原文"])
        self.assertNotIn("累計天數", record)
        self.assertEqual(len(trace), 1)
        self.assertEqual(text, DIRECT_REPLIES["no_evidence"])

    def test_format_failure_has_one_schema_constrained_retry(self):
        q = "我目前幾枚星星幣？"
        text, trace, events, _ = self.run_case(q, select(call("get_my_event_progress", edition="current")),
            {"content": '{"answer":"80"}'},
            response(supported(q, "80 枚星星幣", (f"{CURRENT}/my-progress", '"星星幣": 80'))))
        self.assertIn("80 枚星星幣", text)
        self.assertEqual(len(trace), 1)
        self.assertIn("Python 回答格式修正完成", [title for title, _ in events])
        text, trace, _, _ = self.run_case(q, select(call("get_my_event_progress", edition="current")),
            {"content": "{}"}, {"content": "{}"})
        self.assertEqual(text, ANSWER_VERIFICATION_FAILED)

    def test_repeated_tool_call_stops_without_new_data(self):
        q = "披風能跨服送嗎？"
        repeat = select(rules("跨服贈送"))
        want = response(action="retrieve", missing="查詢贈送政策")
        text, trace, events, _ = self.run_case(q, repeat, want, repeat, want)
        self.assertEqual(len(trace), 1)
        self.assertIn(UNRESOLVED, text)
        self.assertIn("Python 重用工具結果", [title for title, _ in events])
        self.assertIn("Python 停止補查", [title for title, _ in events])

    def test_total_tool_budget_counts_attempts_and_limits_batches(self):
        q = "查活動規則"
        batches = [select(*(rules(f"不同查詢{batch}-{i}") for i in range(6))) for batch in range(2)]
        want = response(action="retrieve", missing="其他資料")
        text, trace, events, model = self.run_case(q, batches[0], want, batches[1], want)
        self.assertEqual(len(trace), MAX_TOOL_CALLS)
        self.assertLessEqual(model.call_count, MAX_TOOL_ROUNDS * 2)
        self.assertIn(UNRESOLVED, text)

    def test_local_tool_failure_does_not_block_independent_data(self):
        q = "商城及我的餘額"
        original_execute = ToolRegistry.execute
        def execute(registry, name, args):
            if name == "list_shop_items":
                raise RuntimeError("SECRET_DSN")
            return original_execute(registry, name, args)
        with patch.object(ToolRegistry, "execute", execute):
            text, trace, events, model = self.run_case(q,
                select(call("list_shop_items", edition="current"), call("get_my_event_progress", edition="current")),
                response(abstain("商城", "unavailable"), supported("餘額", "80 枚星星幣",
                         (f"{CURRENT}/my-progress", '"星星幣": 80'))))
        self.assertEqual([t["工具"] for t in trace], ["get_my_event_progress"])
        self.assertIn("80 枚星星幣", text)
        self.assertNotIn("SECRET_DSN", str(events))
        self.assertNotIn("SECRET_DSN", str(model.call_args))

    def test_calculation_cannot_execute_code_or_reference_unknown_sources(self):
        self.assertEqual(calculate("(80+20)/2"), "50")
        for expr in ("2**1000", "__import__('os')", "1/0", "10**100", "1e1000"):
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                calculate(expr)
        with self.assertRaises(ValueError):
            ToolRegistry("A").validate("calculate", {"expression": "1+2", "source_ids": ["unknown"]},
                                       RequestContext.build("計算", []), {})

    def test_transport_separates_tool_definitions_and_response_schema(self):
        for provider in ("ollama", "openai"):
            with self.subTest(provider=provider), patch("model_client.model_for") as factory:
                model = factory.return_value
                model.bind_tools.return_value.invoke.return_value = AIMessage(content="")
                model.with_structured_output.return_value.invoke.return_value = {
                    "raw": AIMessage(content="{}"), "parsed": {}, "parsing_error": None}
                tools = ToolRegistry("A").definitions()
                completion(provider, [{"role": "user", "content": "test"}], tools)
                model.bind_tools.assert_called_once_with(
                    tools, **({"strict": True} if provider == "openai" else {}))
                model.with_structured_output.assert_not_called()
                completion(provider, [{"role": "user", "content": "test"}], [],
                           response_schema=ANSWER_SCHEMA)
                model.with_structured_output.assert_called_once_with(
                    ANSWER_SCHEMA, method="json_schema", include_raw=True,
                    **({"strict": True} if provider == "openai" else {}))
                with self.assertRaises(ValueError):
                    completion(provider, [], tools, response_schema=ANSWER_SCHEMA)

    def test_answer_ignores_inactive_missing_field_without_model_retry(self):
        question = "我的餘額多少？"
        part = supported(question, "80 枚", (f"{CURRENT}/my-progress", '"星星幣": 80'))
        for missing in ("none", "不需要補查", "  "):
            with self.subTest(missing=missing):
                text, trace, events, model = self.run_case(
                    question, select(call("get_my_event_progress", edition="current")),
                    response(part, missing=missing))
                self.assertEqual(text, "80 枚")
                self.assertEqual(model.call_count, 2)
                self.assertEqual(len(trace), 1)

    def test_duplicate_evidence_is_sent_once_and_duplicate_calls_execute_once(self):
        question = "新手如何參加活動？"
        def finish(provider, messages, tools, **kwargs):
            rows = [row["段落ID"] for src in packet(messages)["工具結果"] for row in src["段落"]]
            self.assertEqual(len(rows), len(set(rows)))
            key = f"{CURRENT}/launch/參加"
            return response(supported(question, original(key), (key, original(key))))
        text, trace, events, _ = self.run_case(question, select(
            rules("報名", "search"), rules("報名", "search"), rules("參加", "eligibility")), finish)
        self.assertEqual(len(trace), 2)
        self.assertIn("Python 重用工具結果", [title for title, _ in events])
        self.assertIn(original(f"{CURRENT}/launch/參加"), text)

    def test_format_retry_provides_specific_error_and_original_output(self):
        q = "我的餘額？"
        def fix(provider, messages, tools, **kwargs):
            feedback = packet(messages)["修正回饋"]
            self.assertIn("results", feedback["錯誤"])
            self.assertEqual(feedback["前次輸出"], '{"action":"answer","missing":"","results":[]}')
            return response(supported(q, "80 枚", (f"{CURRENT}/my-progress", '"星星幣": 80')))
        text, _, _, _ = self.run_case(q, select(call("get_my_event_progress", edition="current")),
            {"content": '{"action":"answer","missing":"","results":[]}'}, fix)
        self.assertEqual(text, "80 枚")


class FixtureReplayTests(OfflineTests):
    """Every original fixture exercises real retrieval and the new evidence contract.

    The model's tool choice is scripted from fixture metadata. This is NOT a
    language-model accuracy score. Live tests are a separate opt-in suite.
    """
    def test_all_existing_cases_replay_through_local_tools(self):
        cases = json.loads((Path(__file__).resolve().parents[1] / "rag_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                q, mode = case["question"], case["mode"]
                player = case.get("player", "A")
                calls = []
                for edition in case["editions"]:
                    if mode == "shop":
                        calls.append(call("list_shop_items", edition=edition))
                    elif mode not in ("progress", "outside-account"):
                        calls.append(rules(case.get("retrieval_query", q),
                                           "search" if mode == "unanswerable" else mode, edition))
                    if case.get("progress"):
                        calls.append(call("get_my_event_progress", edition=edition))

                def finalize(provider, messages, tools, **kwargs):
                    rows = evidence_rows(messages)
                    self.assertLessEqual({source_alias(key) for key in case["sources"]}, set(rows), case["id"])
                    if case["sources"]:
                        refs = [(key, rows[source_alias(key)]["原文"]) for key in case["sources"]]
                        return response(supported(q, "\n".join(v for _, v in refs), *refs))
                    if mode == "outside-account":
                        return response(abstain(q, "account_limit"))
                    if mode == "unanswerable" or case["id"] == "unrecorded-days":
                        return response(abstain(q))
                    self.assertTrue(rows)
                    key, row = next(iter(rows.items()))
                    return response(supported(q, row["原文"], (key, row["原文"])))

                def accept_review(provider, messages, tools, **kwargs):
                    self.assertEqual(kwargs.get("response_schema"), REVIEW_SCHEMA)
                    draft = packet(messages)["回答草稿"]
                    adopted = {key for part in draft["results"] if part["status"] == "supported"
                               for key in part["source_ids"]}
                    self.assertLessEqual({source_alias(key) for key in case["sources"]}, adopted)
                    return {"content": json.dumps({"decision": "keep", "reason": "測試草稿已含參考來源",
                                                    "missing_ids": [], "results": []}, ensure_ascii=False)}

                choice = select(*calls) if calls else {"content": "無需查詢"}
                model = scripted(choice, finalize, accept_review)
                events = []
                with patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_ANSWER_REVIEW": "auto"}):
                    text, trace = asyncio.run(answer(player, "ollama", [], q, model,
                        lambda title, data: events.append((title, data))))
                self.assertEqual(model.call_count, 2 + sum(title == "Python 啟動回答品質審查" for title, _ in events))
                checks = [data for title, data in events if title == "Python 檢查回答依據"]
                adopted = {cite["id"] for part in checks[-1]["子問題"] if part["判定"] == "supported"
                           for cite in part["採用段落"]}
                self.assertLessEqual(set(case["sources"]), adopted)
                self.assertEqual(len(trace), len(calls))
                self.assertNotIn(ANSWER_VERIFICATION_FAILED, text)
                self.assertEqual({t["參數"]["edition"] for t in trace}, set(case["editions"]))
                if case.get("progress"):
                    records = [json.loads(t["結果"]) for t in trace if t["工具"] == "get_my_event_progress"]
                    self.assertEqual(records, [progress(player, e) for e in case["editions"]])
                if mode == "outside-account":
                    self.assertEqual(text, DIRECT_REPLIES["account_limit"])


if __name__ == "__main__":
    unittest.main()
