"""Response quality review contracts using real tools and scripted model replies."""
import asyncio
import json
import os
from unittest.mock import patch

from agent import answer
from answer_quality import REVIEW_SCHEMA
from answering import DIRECT_REPLIES
from eval_rag import live_source_coverage
from guide import CATALOG, published_passages
from tests.test_bounded_agent import (abstain, call, fallback, original, packet, response,
                                rules, scripted, supported)
from tests.test_guide import OfflineTests


CURRENT = CATALOG.current


def review(decision, *parts, missing=(), reason="審查完成"):
    return {"content": json.dumps({"decision": decision, "reason": reason,
                                   "missing_ids": list(missing), "results": list(parts)}, ensure_ascii=False)}


class QualityTests(OfflineTests):
    def setUp(self):
        super().setUp()
        config = patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_TOOL_SELECTION": "structured",
                                         "EVENT_SUPPORT_AGENT_ANSWER_REVIEW": "auto"})
        config.start()
        self.addCleanup(config.stop)

    def ask(self, question, *replies):
        model = scripted(*replies)
        events = []
        text, trace = asyncio.run(answer("A", "ollama", [], question, model,
                                        lambda title, data: events.append((title, data))))
        self.assertEqual(model.call_count, len(replies))
        return text, trace, events, model

    def test_real_failure_shape_review_recovers_missing_main_section(self):
        # Replay the observed 2026-11-24 trace: 4 supported fragments, all valid
        # individually, but the actual requested activity flow is incomplete.
        question = "這個活動要怎麼玩?"
        launch = [p for p in published_passages(CURRENT) if p["文件ID"] == "launch"]
        missing = launch[-1]["段落ID"]
        first = [launch[0]["段落ID"], launch[1]["段落ID"],
                 f"{CURRENT}/match/練習與掃蕩", f"{CURRENT}/match/斷線與重連"]
        draft = response(*(supported(question, original(key), (key, original(key))) for key in first))

        def revise(provider, messages, tools, **kwargs):
            self.assertEqual(tools, [])
            self.assertEqual(kwargs.get("response_schema"), REVIEW_SCHEMA)
            review_data = packet(messages)
            self.assertIn(missing, review_data["審查線索"]["同文件未採用"])
            self.assertTrue(review_data["審查線索"]["重複問題"])
            return review("revise", supported(question, "\n".join(p["原文"] for p in launch),
                          *((p["段落ID"], p["原文"]) for p in launch)), missing=[missing])

        text, trace, events, model = self.ask(question, fallback(rules("玩法")), draft, revise)
        self.assertEqual(len(trace), 1)
        self.assertTrue(all(p["原文"] in text for p in launch))
        self.assertNotIn(original(first[-1]), text)
        self.assertEqual(dict(events)["Python 回答品質審查結果"]["判定"], "revised")
        coverage = live_source_coverage([p["段落ID"] for p in launch], events)
        self.assertEqual(coverage["retrieval_coverage"], 1.0)
        self.assertEqual(coverage["answer_coverage"], 1.0)
        self.assertEqual(model.call_count, 3)

    def test_specific_question_does_not_need_all_document_sections(self):
        question = "十一月排位有計算嗎？"
        key = f"{CURRENT}/launch/玩法"
        draft = response(supported(question, "排位不計。", (key, "排位不計")))
        text, trace, events, _ = self.ask(question, fallback(rules("排位")), draft,
                                         review("keep", reason="其他章節不影響所問排位規則"))
        self.assertIn("排位不計。", text)
        self.assertNotIn("披風", text)
        self.assertEqual(dict(events)["Python 回答品質審查結果"]["判定"], "kept")
        self.assertEqual(len(trace), 1)

    def test_complete_single_evidence_answer_does_not_review(self):
        question = "我的星星幣有多少？"
        text, trace, events, _ = self.ask(question, fallback(call("get_my_event_progress", edition="current")),
            response(supported(question, "80 枚星星幣", (f"{CURRENT}/my-progress", '"星星幣": 80'))))
        self.assertIn("80 枚星星幣", text)
        self.assertNotIn("Python 啟動回答品質審查", dict(events))

    def test_review_cannot_invent_source_or_drop_committed_evidence(self):
        question = "請介紹本期活動"
        key, missing = f"{CURRENT}/launch/玩法", f"{CURRENT}/launch/兌換"
        draft = response(supported(question, original(key), (key, original(key))))
        bad_reviews = [
            review("revise", supported(question, "錯誤內容", ("invented", "假")), missing=["invented"]),
            review("revise", supported(question, original(key), (key, original(key))), missing=[missing]),
            review("revise", abstain(question)),
        ]
        for bad in bad_reviews:
            with self.subTest(bad=bad):
                text, trace, events, _ = self.ask(question, fallback(rules(mode="overview")), draft, bad)
                self.assertIn(original(key), text)
                self.assertNotIn("錯誤內容", text)
                self.assertEqual(dict(events)["Python 回答品質審查結果"]["判定"], "review_failed")

    def test_review_preserves_independent_answer_and_account_refusal(self):
        question = "我的餘額以及活動規則，另外幫查其他人"
        q1, q2, q3 = "我的餘額", "活動規則", "別人餘額"
        key, record_id = f"{CURRENT}/launch/玩法", f"{CURRENT}/my-progress"
        balance = supported(q1, "80 枚星星幣", (record_id, '"星星幣": 80'))
        policy = supported(q2, original(key), (key, original(key)))
        refusal = abstain(q3, "account_limit")
        draft = response(balance, policy, refusal)
        for replacement in ([policy, refusal], [balance, policy],
                            [balance, policy, supported(q3, "80 枚", (record_id, '"星星幣": 80'))]):
            with self.subTest(replacement=replacement):
                text, trace, events, _ = self.ask(question,
                    fallback(rules(mode="overview"), call("get_my_event_progress", edition="current")),
                    draft, review("revise", *replacement))
                self.assertIn("80 枚星星幣", text)
                self.assertIn(DIRECT_REPLIES["account_limit"], text)
                self.assertEqual(dict(events)["Python 回答品質審查結果"]["判定"], "review_failed")

    def test_review_transport_failure_preserves_draft_without_retry(self):
        question = "介紹本期活動"
        key = f"{CURRENT}/launch/玩法"
        def unavailable(*args, **kwargs):
            raise TimeoutError("review unavailable")
        text, trace, events, _ = self.ask(question, fallback(rules(mode="overview")),
            response(supported(question, original(key), (key, original(key)))), unavailable)
        self.assertIn(original(key), text)
        self.assertEqual(dict(events)["Python 回答品質審查結果"]["判定"], "review_failed")

    def test_coverage_reports_retrieved_but_omitted_and_withheld_separately(self):
        required = ["a", "b", "c"]
        events = [
            ("收到工具回傳 · find_rules", {"isError": False, "結構化資料": {
                "result": [{"段落ID": key} for key in required]}}),
            ("Python 核對公告適用條件", {"未採用段落": [{"段落ID": "c"}]}),
            ("Python 檢查回答依據", {"子問題": [
                {"判定": "supported", "採用段落": [{"id": "a"}]},
                {"判定": "rejected", "採用段落": [{"id": "b"}]}]}),
        ]
        report = live_source_coverage(required, events)
        self.assertEqual(report["missing_retrieval"], ["c"])
        self.assertEqual(report["missing_answer"], ["b", "c"])
        self.assertAlmostEqual(report["retrieval_coverage"], 2 / 3)
        self.assertAlmostEqual(report["answer_coverage"], 1 / 3)
        self.assertIsNone(live_source_coverage([], events)["answer_coverage"])
