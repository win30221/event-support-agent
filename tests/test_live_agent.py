"""Opt-in integration tests; requires a real local Ollama model.

These assert tool scope and evidence coverage. Read the answers against the
fixture's reference separately to assess semantic correctness/completeness.
"""
import asyncio
import json
import os
import unittest
from pathlib import Path

from agent import answer
from answering import ANSWER_VERIFICATION_FAILED, DIRECT_REPLIES
from guide import CATALOG, published_passages, shop_catalog
from evaluations.eval_rag import live_source_coverage


@unittest.skipUnless(os.getenv("EVENT_SUPPORT_AGENT_LIVE_OLLAMA") == "1", "需啟用本機 Ollama 實測")
class LiveOllamaTests(unittest.TestCase):
    def ask(self, question, history=(), player="A"):
        events = []
        reply, trace = asyncio.run(answer(player, "ollama", list(history), question,
                                        observe=lambda title, data: events.append((title, data))))
        self.assertNotIn(ANSWER_VERIFICATION_FAILED, reply)
        return reply, trace, events

    def test_representative_dialogues(self):
        first = "這個活動要怎麼玩?"
        reply, trace, events = self.ask(first)
        required = {p["段落ID"] for p in published_passages(CATALOG.current) if p["文件ID"] == "launch"}
        checks = [data for title, data in events if title == "Python 檢查回答依據"]
        adopted = {cite["id"] for check in checks for part in check["子問題"]
                   if part["判定"] == "supported" for cite in part["採用段落"]}
        self.assertLessEqual(required, adopted, reply)
        self.assertNotIn(DIRECT_REPLIES["no_evidence"], reply)
        self.assertLessEqual(reply.count(first), 1, reply)
        history = [{"role": "user", "content": first}, {"role": "assistant", "content": reply, "trace": trace}]
        reply, trace, _ = self.ask("那其他價格多少?", history)
        self.assertTrue(any(t["工具"] == "list_shop_items" for t in trace), reply)
        self.assertFalse(any(t["工具"] == "get_my_event_progress" for t in trace), reply)
        for item in shop_catalog()["items"][1:]:
            self.assertIn(item["name"], reply)
        reply, trace, _ = self.ask("幫我查 B 的星星幣餘額")
        self.assertFalse(any(t["工具"] == "get_my_event_progress" for t in trace), reply)
        self.assertIn(DIRECT_REPLIES["account_limit"], reply)

    def test_explicit_negative_lookup_is_answered_with_verified_evidence(self):
        for name in ("特斯拉", "銀河戰車"):
            with self.subTest(name=name):
                question = f"我想兌換{name}怎麼失敗？"
                reply, trace, events = self.ask(question)
                self.assertTrue(any(t["工具"] == "find_shop_item" for t in trace), reply)
                self.assertFalse(any(t["工具"] == "get_my_event_progress" for t in trace), reply)
                self.assertIn(name, reply)
                self.assertNotIn(DIRECT_REPLIES["no_evidence"], reply)
                checks = [data for title, data in events if title == "Python 檢查回答依據"]
                self.assertTrue(checks and any(part["判定"] == "supported" and
                    any("/shop-status/" in cite["id"] for cite in part["採用段落"])
                    for part in checks[-1]["子問題"]), reply)

    def test_fixture_retrieval_and_account_scope(self):
        cases = json.loads((Path(__file__).resolve().parents[1] / "evaluations" / "rag_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                history = []
                for q in [*case.get("before", []), case["question"]]:
                    reply, trace, events = self.ask(q, history, case.get("player", "A"))
                    history.extend([{"role": "user", "content": q},
                                    {"role": "assistant", "content": reply, "trace": trace}])
                keys = {t["參數"]["edition"] for t in trace if "edition" in t["參數"]}
                self.assertLessEqual(keys, set(case["editions"]), reply)
                if case.get("progress"):
                    self.assertTrue(any(t["工具"] == "get_my_event_progress" for t in trace), reply)
                if case["mode"] == "outside-account":
                    self.assertFalse(any(t["工具"] == "get_my_event_progress" for t in trace), reply)
                if case["mode"] == "shop":
                    self.assertTrue(any(t["工具"] == "list_shop_items" for t in trace), reply)
                if case["sources"]:
                    coverage = live_source_coverage(case["sources"], events)
                    self.assertFalse(coverage["missing_retrieval"],
                                     f"檢索遺漏 {coverage['missing_retrieval']}：{reply}")
                    self.assertFalse(coverage["missing_answer"],
                                     f"回答未採用 {coverage['missing_answer']}：{reply}")


if __name__ == "__main__":
    unittest.main()
