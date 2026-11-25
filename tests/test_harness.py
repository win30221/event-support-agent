"""Graph lifecycle and bounded Stop-gate regressions.

Real registry/data, scripted models. No test uses a network or real LLM.
"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from agent import MAX_TOOL_ROUNDS, answer
from answering import DIRECT_REPLIES
from guide import CATALOG
from tests.test_bounded_agent import (abstain, call, evidence_rows, select, original, packet,
                                response, rules, scripted, supported)
from tests.test_answer_quality import review
from tests.test_guide import OfflineTests


CURRENT = CATALOG.current


class HarnessTests(OfflineTests):
    def setUp(self):
        super().setUp()
        config = patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_STOP_GATE": "auto", "EVENT_SUPPORT_AGENT_ANSWER_REVIEW": "off"})
        config.start()
        self.addCleanup(config.stop)

    def ask(self, question, *replies, player="A"):
        events = []
        model = scripted(*replies)
        text, trace = asyncio.run(answer(player, "ollama", [], question, model,
                                        lambda title, data: events.append((title, data))))
        self.assertEqual(model.call_count, len(replies))
        return text, trace, events

    def contradictory(self, question):
        p = abstain(question)
        p["answer"] = "未經證實的推論；請勿直接顯示。"
        p["citations"] = [{"id": f"{CURRENT}/launch/兌換",
                           "quote": original(f"{CURRENT}/launch/兌換")}]
        return p

    def test_lifecycle_and_no_extra_model_call_for_supported_balance(self):
        q = "我的餘額多少？"
        reply, trace, events = self.ask(q,
            select(call("get_my_event_progress", edition="current")),
            response(supported(q, "80 枚", (f"{CURRENT}/my-progress", '"星星幣": 80'))))
        self.assertIn("80 枚", reply)
        self.assertEqual(len(trace), 1)
        self.assertEqual([d["節點"] for t, d in events if t == "Python Harness 節點"],
                         ["select", "execute", "assess", "stop_gate", "finalize"])

    def test_observed_wrong_tools_then_stop_recheck(self):
        for name in ("特斯拉", "銀河戰車"):
            with self.subTest(name=name):
                q = f"我想兌換{name}怎麼失敗?"
                def select_again(provider, messages, tools, **kwargs):
                    payload = packet(messages)
                    self.assertIn("重新核對", payload["系統補查回饋"])
                    self.assertTrue(any(m["role"] == "tool" for m in messages))
                    return select(call("find_shop_item", edition="current", name=name))
                def finish(provider, messages, tools, **kwargs):
                    row = next(r for r in evidence_rows(messages).values() if r.get("資料狀態") == "not_found")
                    # Deliberately mislabeled again: the prior guarded
                    # normalization must still render the host's lookup fact.
                    p = abstain(q)
                    p["answer"] = "不可靠內容：已扣款。"
                    p["citations"] = [{"id": row["段落ID"], "quote": row["原文"]}]
                    return response(p)
                reply, trace, events = self.ask(q,
                    select(rules(name, "eligibility"), call("get_my_event_progress", edition="current")),
                    response(self.contradictory(q)), select_again, finish)
                self.assertIn(f"商城清單查無「{name}」", reply)
                self.assertNotIn("已扣款", reply)
                self.assertNotIn("未經證實的推論", reply)
                self.assertEqual([t["工具"] for t in trace],
                                 ["find_rules", "get_my_event_progress", "find_shop_item"])
                self.assertEqual(sum(t == "Python 停止檢查" for t, _ in events), 1)

    def test_stop_gate_does_not_loop_when_model_repeats_same_query(self):
        q = "沒有寫的規則如何處理？"
        reply, trace, events = self.ask(q, select(rules(mode="overview")),
            response(self.contradictory(q)), select(rules(mode="overview")))
        self.assertEqual(reply, DIRECT_REPLIES["no_evidence"])
        self.assertEqual(len(trace), 1)
        self.assertEqual(dict(events)["Python 停止檢查結果"]["判定"], "no_progress")

    def test_stop_gate_does_not_force_lookup_when_no_tool_is_appropriate(self):
        q = "某個未記錄的交易怎麼處理？"
        reply, trace, events = self.ask(q, select(rules(mode="overview")),
            response(self.contradictory(q)), select())
        self.assertEqual(reply, DIRECT_REPLIES["no_evidence"])
        self.assertEqual(len(trace), 1)

    def test_stop_gate_off_preserves_original_safe_response(self):
        q = "我想兌換銀河戰車怎麼失敗?"
        with patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_STOP_GATE": "off"}):
            reply, trace, events = self.ask(q, select(rules(mode="overview")),
                                           response(self.contradictory(q)))
        self.assertEqual(reply, DIRECT_REPLIES["no_evidence"])
        self.assertNotIn("Python 停止檢查", dict(events))

    def test_invalid_citations_and_clean_abstentions_do_not_trigger(self):
        q = "有跨服贈送手續費嗎？"
        bad = self.contradictory(q)
        bad["citations"][0]["id"] = "invented"
        for p in (bad, abstain(q)):
            with self.subTest(part=p):
                reply, trace, events = self.ask(q, select(rules(mode="overview")), response(p))
                self.assertEqual(reply, DIRECT_REPLIES["no_evidence"])
                self.assertNotIn("Python 停止檢查", dict(events))

    def test_stop_gate_does_not_reopen_mixed_answer_or_refusal(self):
        q = "我的餘額、未記錄的條款以及別人的餘額"
        reply, trace, events = self.ask(q,
            select(rules(mode="overview"), call("get_my_event_progress", edition="current")),
            response(supported("我的餘額", "80 枚", (f"{CURRENT}/my-progress", '"星星幣": 80')),
                     self.contradictory("未記錄的條款"), abstain("別人的餘額", "account_limit")))
        self.assertIn("80 枚", reply)
        self.assertIn(DIRECT_REPLIES["no_evidence"], reply)
        self.assertIn(DIRECT_REPLIES["account_limit"], reply)
        self.assertNotIn("Python 停止檢查", dict(events))

    def test_stop_gate_retry_cannot_repeat_with_new_but_insufficient_data(self):
        q = "其他未記載的政策？"
        reply, trace, events = self.ask(q, select(rules(mode="overview")),
            response(self.contradictory(q)), select(rules("跨服", "search")),
            response(self.contradictory(q)))
        self.assertEqual(reply, DIRECT_REPLIES["no_evidence"])
        self.assertEqual(sum(t == "Python 停止檢查" for t, _ in events), 1)
        self.assertLessEqual(max(d["輪次"] for t, d in events if t == "Python Harness 節點"), MAX_TOOL_ROUNDS)

    def test_gate_does_not_open_when_already_at_round_limit(self):
        q = "我想查幾個不同公告的限制"
        reply, trace, events = self.ask(q,
            select(rules(mode="overview")), response(action="retrieve", missing="需要檢索"),
            select(rules("跨服", "search")), response(action="retrieve", missing="還需另一檔"),
            select(rules(mode="overview", edition="previous")), response(self.contradictory(q)))
        self.assertEqual(reply, DIRECT_REPLIES["no_evidence"])
        self.assertEqual(len(trace), MAX_TOOL_ROUNDS)
        self.assertNotIn("Python 停止檢查", dict(events))

    def test_keep_with_extra_results_does_not_consume_replacement(self):
        q = "排位有計算嗎？"
        key = f"{CURRENT}/launch/玩法"
        with patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_ANSWER_REVIEW": "auto"}):
            reply, trace, events = self.ask(q, select(rules(mode="overview")),
                response(supported(q, "排位不計。", (key, "排位不計"))),
                review("keep", supported(q, "偽造內容", ("invented", "假的"))))
        self.assertEqual(reply, "排位不計。")
        self.assertEqual(dict(events)["Python 回答品質審查結果"]["判定"], "kept")
        self.assertIn("Python 捨棄審查非作答欄位", dict(events))

    def test_concurrent_turns_keep_distinct_account_evidence(self):
        q = "我今天已計入幾場？"
        def run(player, count):
            def finish(provider, messages, tools, **kwargs):
                row = evidence_rows(messages)[f"{CURRENT}/my-progress"]
                self.assertIn(f'"今日已計入場次": {count}', row["原文"])
                return response(supported(q, f"{count} 場",
                                (row["段落ID"], f'"今日已計入場次": {count}')))
            return self.ask(q, select(call("get_my_event_progress", edition="current")),
                            finish, player=player)[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(run, "A", 2)
            b = pool.submit(run, "B", 3)
            self.assertEqual(a.result(), "2 場")
            self.assertEqual(b.result(), "3 場")
