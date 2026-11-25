"""第一層的輸出契約與獨立評測入口；不以 mock 宣稱 LLM 分類正確率。"""
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from eval_intent import classify, compare, evaluate_cases, load_cases, main, matches
from intent_analysis import INTENT_PROMPT, INTENT_SCHEMA, SimpleIntentPlan, anchor_intents
from task_runtime import IntentRuntime


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
        packet = json.loads(messages[-1]["content"])
        self.assertEqual(packet["玩家原問"], question)
        self.assertEqual(set(packet), {"玩家原問", "服務上下文", "修正回饋"})
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


    def test_prompt_defines_each_intent_without_omitting_agreed_sections(self):
        # 保護約定的 Prompt 結構，不以檢查文字宣稱模型語意正確。
        for category in ("knowledge", "query", "analysis", "operation", "other"):
            with self.subTest(category=category):
                section = INTENT_PROMPT.split(f"【{category}】", 1)[1].split("【", 1)[0]
                for label in ("定義：", "適用：", "不適用：", "判斷原則：", "對照範例："):
                    self.assertIn(label, section)
                self.assertIn("\n不適用：", section)

    def test_production_classifier_omits_authority_but_audit_keeps_it(self):
        question = "替我領取活動獎勵"
        complete = Mock(side_effect=[
            {"content": json.dumps({"intents": [{"type": "operation", "request": question}]})},
            {"content": json.dumps({"decision": "accept", "issues": [], "bindings": [
                {"task_id": "T1", "kind": "operation", "editions": [], "product": "", "account": "self"}
            ]})},
        ])
        runtime = IntentRuntime("A", "ollama", [], question, complete, lambda *_: None)
        with patch("backend.ToolRegistry.execute", side_effect=AssertionError("不應執行資料工具")):
            result = asyncio.run(runtime.intent(runtime.initial_state()))
        self.assertEqual(complete.call_count, 2)
        first, second = [json.loads(call.args[1][-1]["content"]) for call in complete.call_args_list]
        self.assertEqual(set(first), {"玩家原問", "服務上下文", "修正回饋"})
        self.assertIn("無寫入或其他帳號工具", second["能力"])
        self.assertIn("允許業務能力", second)
        self.assertEqual(result["task_states"]["T1"]["status"], "unsupported")

    def test_comparison_separates_wrong_type_wrong_span_and_invalid_contract(self):
        expected = [{"type": "query", "request": "查點數。"}, {"type": "knowledge", "request": "怎麼參加？"}]
        wrong_type = {"contract_valid": True, "intents": [
            {"type": "analysis", "request": "查點數。"}, expected[1]]}
        wrong_span = {"contract_valid": True, "intents": [
            {"type": "query", "request": "查點數"}, expected[1]]}
        self.assertEqual(compare(wrong_type, expected), {"count_match": True,
                         "type_sequence_match": False, "span_match": True, "exact_match": False})
        self.assertEqual(compare(wrong_span, expected), {"count_match": True,
                         "type_sequence_match": True, "span_match": False, "exact_match": False})
        self.assertFalse(any(compare({"contract_valid": False, "intents": None}, expected).values()))
        self.assertFalse(compare({"contract_valid": True, "intents": expected[:1]}, expected)["count_match"])

    def test_select_single_case(self):
        self.assertEqual([c["id"] for c in load_cases("diagnosis")], ["diagnosis"])
        with self.assertRaises(ValueError):
            load_cases("invalid-case-id")

    def test_repeat_batch_preserves_failure_and_does_not_send_expected(self):
        case = {"id": "local", "question": "查點數。", "expected": [
            {"type": "query", "request": "查點數。"}], "rationale": "既有紀錄"}
        good = {"question": case["question"], "contract_valid": True, "intents": case["expected"]}
        classify_mock = AsyncMock(side_effect=[good, RuntimeError("模型服務無法使用")])
        with patch("eval_intent.classify", classify_mock), patch("eval_intent.print_result"), patch("builtins.print"):
            report = asyncio.run(evaluate_cases([case], "ollama", 2))
        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["exact_match"], 1)
        self.assertEqual(report["summary"]["contract_valid"], 1)
        self.assertIsNone(report["results"][1]["model_calls"])
        self.assertEqual([c.args for c in classify_mock.call_args_list],
                         [(case["question"], "ollama", []), (case["question"], "ollama", [])])

    def test_holdout_cases_are_distinct_valid_and_not_in_prompt(self):
        baseline, holdout = load_cases(), load_cases(suite="holdout")
        self.assertEqual(len(baseline), 36)
        self.assertEqual(len(holdout), 20)
        self.assertEqual(len({c["id"] for c in holdout}), len(holdout))
        self.assertEqual(len({c["question"] for c in holdout}), len(holdout))
        self.assertFalse({c["id"] for c in baseline} & {c["id"] for c in holdout})
        self.assertFalse({c["question"] for c in baseline} & {c["question"] for c in holdout})
        self.assertEqual({i["type"] for c in holdout for i in c["expected"]},
                         {"knowledge", "query", "analysis", "operation", "other"})
        for case in holdout:
            with self.subTest(case=case["id"]):
                self.assertNotIn(case["question"], INTENT_PROMPT)
                plan = SimpleIntentPlan.model_validate({"intents": case["expected"]})
                self.assertTrue(matches({"intents": anchor_intents(plan, case["question"])},
                                        case["expected"]))
                self.assertTrue(case["rationale"])
        self.assertEqual(load_cases(holdout[0]["id"], suite="holdout"), [holdout[0]])
        with self.assertRaises(ValueError):
            load_cases(holdout[0]["id"])
        with self.assertRaises(ValueError):
            load_cases(baseline[0]["id"], suite="holdout")

    def test_holdout_main_evaluates_only_new_suite(self):
        # Mock 驗證 CLI 選擇題集，不呼叫真實 LLM 或重跑已通過的題目。
        args = SimpleNamespace(cases=True, case=None, suite="holdout",
                               provider="ollama", repeat=1, report=None, question=None)
        evaluator = AsyncMock(return_value={"summary": {"total": 20, "exact_match": 20}})
        with patch("eval_intent.evaluate_cases", evaluator), patch("builtins.print"):
            self.assertEqual(asyncio.run(main(args)), 0)
        evaluator.assert_awaited_once_with(load_cases(suite="holdout"), "ollama", 1)
