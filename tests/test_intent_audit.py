"""第二層契約、標記題庫與共用入口；mock 結果不代表 LLM 實機準確率。"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from evaluations.eval_audit import compare, evaluate_cases, load_cases, review
from intent_analysis import (AUDIT_PROMPT, AUDIT_SCHEMA, CAPABILITIES, IntentAudit,
                             SimpleIntentPlan, anchor_intents, bind_intents)
from request_context import RequestContext
from task_runtime import IntentRuntime


def fixture_audit(case):
    # 標記包含獨立預期的主機狀態；檔期 unavailable 是模型輸入，主機核定後為空。
    expected = case["expected"]
    if expected["decision"] == "revise":
        return {"decision": "revise", "issues": [case["rationale"]], "bindings": []}
    bindings = []
    for t in expected["tasks"]:
        binding = {k: t[k] for k in ("task_id", "kind", "editions", "product", "account")}
        if t["status"] == "unavailable":
            binding["editions"] = ["unavailable"]
        bindings.append(binding)
    return {"decision": "accept", "issues": [], "bindings": bindings}


class IntentAuditTests(unittest.TestCase):
    def test_fixture_contract_and_expected_host_states(self):
        cases = load_cases()
        self.assertEqual(len(cases), 24)
        self.assertEqual(len({c["id"] for c in cases}), 24)
        self.assertEqual(sum(c["expected"]["decision"] == "revise" for c in cases), 5)
        self.assertEqual({t["kind"] for c in cases for t in c["expected"]["tasks"]},
                         set().union(*CAPABILITIES.values()))
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertNotIn(case["question"], AUDIT_PROMPT)
                intents = anchor_intents(SimpleIntentPlan.model_validate({"intents": case["intents"]}),
                                         case["question"])
                context = RequestContext.build(case["question"], case.get("history", []))
                raw = fixture_audit(case)
                plan = bind_intents(IntentAudit.model_validate(raw), intents, context, lambda *_: None)
                tasks, states = plan if plan is not None else ([], {})
                result = {"contract_valid": True, "audit": raw, "first_layer_intents": intents,
                          "tasks": tasks, "task_states": states}
                self.assertTrue(compare(result, case["expected"])["exact_match"], result)

    def test_standalone_uses_same_audit_and_never_classifies_or_calls_tools(self):
        case = load_cases("other-account")[0]
        complete = Mock(return_value={"content": json.dumps(fixture_audit(case))})
        with patch.object(IntentRuntime, "classify_intents", side_effect=AssertionError("不應重跑第一層")), \
             patch("backend.ToolRegistry.execute", side_effect=AssertionError("不應執行工具")):
            result = asyncio.run(review(case["question"], case["intents"], complete=complete))
        self.assertTrue(compare(result, case["expected"])["exact_match"])
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["task_states"]["T1"]["status"], "account_limit")
        complete.assert_called_once()
        provider, messages, tools = complete.call_args.args
        self.assertEqual(provider, "ollama")
        self.assertEqual(tools, [])
        self.assertEqual(messages[0]["content"], AUDIT_PROMPT)
        self.assertEqual(complete.call_args.kwargs["response_schema"], AUDIT_SCHEMA)
        packet = json.loads(messages[-1]["content"])
        self.assertEqual(set(packet), {"玩家原問", "服務上下文", "能力", "修正回饋", "需求", "允許業務能力"})
        self.assertEqual(packet["需求"], result["first_layer_intents"])
        self.assertNotIn("expected", packet)
        self.assertNotIn("rationale", packet)

    def test_revise_does_not_trigger_first_layer_retry(self):
        case = load_cases("wrong-analysis")[0]
        complete = Mock(return_value={"content": json.dumps(fixture_audit(case))})
        with patch.object(IntentRuntime, "classify_intents", side_effect=AssertionError("不應重跑第一層")):
            result = asyncio.run(review(case["question"], case["intents"], complete=complete))
        self.assertTrue(compare(result, case["expected"])["exact_match"])
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["model_calls"], 1)
        complete.assert_called_once()

    def test_wrong_kind_is_rejected_and_contract_repair_bounded(self):
        case = load_cases("public-comparison")[0]
        raw = fixture_audit(case)
        raw["bindings"][0]["kind"] = "knowledge"
        complete = Mock(return_value={"content": json.dumps(raw)})
        result = asyncio.run(review(case["question"], case["intents"], complete=complete))
        self.assertFalse(result["contract_valid"])
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(len(result["contract_errors"]), 2)
        self.assertFalse(compare(result, case["expected"])["exact_match"])

    def test_wrong_account_mismatch_even_when_contract_valid(self):
        case = load_cases("other-account")[0]
        raw = fixture_audit(case)
        raw["bindings"][0]["account"] = "self"
        raw["bindings"][0]["editions"] = ["current"]
        complete = Mock(return_value={"content": json.dumps(raw)})
        result = asyncio.run(review(case["question"], case["intents"], complete=complete))
        self.assertTrue(result["contract_valid"])
        self.assertFalse(compare(result, case["expected"])["account_match"])
        self.assertFalse(compare(result, case["expected"])["status_match"])
        self.assertFalse(compare(result, case["expected"])["exact_match"])

    def test_batch_counts_accept_and_revise_separately(self):
        case = load_cases("other-account")[0]
        bad = load_cases("wrong-analysis")[0]
        correct = asyncio.run(review(case["question"], case["intents"],
                                     complete=Mock(return_value={"content": json.dumps(fixture_audit(case))})))
        evaluator = AsyncMock(side_effect=[correct, RuntimeError("模型無法使用")])
        with patch("evaluations.eval_audit.review", evaluator), patch("evaluations.eval_audit.print_result"), patch("builtins.print"):
            report = asyncio.run(evaluate_cases([case, bad], "ollama", 1))
        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["accept_cases"], 1)
        self.assertEqual(report["summary"]["revise_cases"], 1)
        self.assertEqual(report["summary"]["exact_match"], 1)
        self.assertEqual(report["summary"]["account_match"], 1)
        self.assertEqual(report["summary"]["revise_decision_match"], 0)
        self.assertEqual(evaluator.call_args_list[0].args, (case["question"], case["intents"], "ollama", []))

    def test_single_case_selection(self):
        self.assertEqual([c["id"] for c in load_cases("public-comparison")], ["public-comparison"])
        with self.assertRaises(ValueError):
            load_cases("not-a-case")
