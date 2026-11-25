"""第一層的輸出契約與獨立評測入口；不以 mock 宣稱 LLM 分類正確率。"""
import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from eval_intent import classify, matches
from intent_analysis import INTENT_PROMPT, INTENT_SCHEMA, SimpleIntentPlan, anchor_intents


class IntentAnalysisTests(unittest.TestCase):
    def test_fixture_labels_and_exact_spans_are_valid(self):
        cases = json.loads((Path(__file__).resolve().parents[1] / "intent_cases.json").read_text(encoding="utf-8"))
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        self.assertEqual({i["type"] for c in cases for i in c["expected"]},
                         {"knowledge", "query", "analysis", "operation", "other"})
        for case in cases:
            with self.subTest(case=case["id"]):
                plan = SimpleIntentPlan.model_validate({"intents": case["expected"]})
                anchored = anchor_intents(plan, case["question"])
                self.assertTrue(matches({"intents": anchored}, case["expected"]))

    def test_standalone_uses_production_prompt_schema_and_no_tools(self):
        question = "我的點數是多少？"
        expected = [{"type": "query", "request": question}]
        complete = Mock(return_value={"content": json.dumps({"intents": expected})})
        with patch("backend.ToolRegistry.execute", side_effect=AssertionError("不應執行資料工具")):
            result = asyncio.run(classify(question, complete=complete))
        self.assertTrue(matches(result, expected))
        self.assertTrue(result["contract_valid"])
        self.assertEqual(result["model_calls"], 1)
        complete.assert_called_once()
        provider, messages, tools = complete.call_args.args
        self.assertEqual(provider, "ollama")
        self.assertEqual(tools, [])
        self.assertEqual(messages[0]["content"], INTENT_PROMPT)
        self.assertEqual(json.loads(messages[-1]["content"])["玩家原問"], question)
        self.assertEqual(complete.call_args.kwargs["response_schema"], INTENT_SCHEMA)

    def test_contract_repair_does_not_rewrite_question(self):
        question = "查我的活動點數。"
        expected = [{"type": "query", "request": question}]
        complete = Mock(side_effect=[
            {"content": json.dumps({"intents": [{"type": "query", "request": "改寫原句"}]})},
            {"content": json.dumps({"intents": expected})},
        ])
        result = asyncio.run(classify(question, complete=complete))
        self.assertTrue(matches(result, expected))
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(len(result["contract_errors"]), 1)
        packet = json.loads(complete.call_args.args[1][-1]["content"])
        self.assertEqual(packet["玩家原問"], question)
        self.assertIn("錯誤", packet)

    def test_invalid_enum_fails_after_bounded_repair(self):
        complete = Mock(return_value={"content": json.dumps(
            {"intents": [{"type": "invalid", "request": "我的點數？"}]})})
        result = asyncio.run(classify("我的點數？", complete=complete))
        self.assertFalse(result["contract_valid"])
        self.assertIsNone(result["intents"])
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(len(result["contract_errors"]), 2)

    def test_history_is_passed_but_not_used_as_latest_question(self):
        question = "那上期呢？"
        history = [{"role": "user", "content": "本期商城價格？"},
                   {"role": "assistant", "content": "本期有五件商品。"}]
        expected = [{"type": "query", "request": question}]
        complete = Mock(return_value={"content": json.dumps({"intents": expected})})
        result = asyncio.run(classify(question, history=history, complete=complete))
        self.assertTrue(matches(result, expected))
        messages = complete.call_args.args[1]
        self.assertEqual(messages[1:-1], history)
        self.assertEqual(json.loads(messages[-1]["content"])["玩家原問"], question)
