"""第二層首輪規劃契約：測試主機驗證，mock 不代表模型準確率。"""
import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from backend import ToolRegistry
from evaluations.eval_task_tools import compare, load_cases
from task_tool_planning import (CONTROL, TASK_TOOL_PROMPT, plan_tools, route_tasks,
                                task_definitions, validate_batch)


def call(tool_name, /, **args):
    return {"function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)}}


def fixture_calls(case):
    calls = []
    for task in case["expected"]:
        tid = task["task_id"]
        if task["status"] in ("clarify", "account_limit", "unavailable", "no_evidence"):
            calls.append(call(CONTROL, task_id=tid, status=task["status"], reason=case["rationale"]))
        for proposal in task["calls"]:
            args = dict(proposal["arguments"], task_id=tid)
            for key in ("fields", "account_scope"):
                if key in proposal:
                    args[key] = proposal[key]
            if proposal["tool"] == "find_rules":
                args["query"] = " ".join(group.split("|")[0] for group in proposal.get("query_terms", []))
            calls.append(call(proposal["tool"], **args))
    return calls


class TaskToolPlanningTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry("A")

    def check(self, case, calls):
        tasks = route_tasks(case["question"], case["intents"])
        return validate_batch({"tool_calls": calls}, tasks, self.registry, case.get("history", []))

    def test_all_fixture_contracts(self):
        cases = load_cases()
        self.assertEqual(len(cases), 20)
        self.assertEqual(len({c["id"] for c in cases}), 20)
        self.assertEqual(sum(len(c["intents"]) for c in cases), 31)
        self.assertEqual([sum(c["group"] == group for c in cases) for group in ("single", "same", "mixed")], [10, 5, 5])
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertNotIn(case["question"], TASK_TOOL_PROMPT)
                result = self.check(case, fixture_calls(case))
                self.assertTrue(result["contract_valid"], result)
                self.assertTrue(compare(result, case["expected"])["exact_match"], result)

    def test_native_call_batch_no_classification_or_execution(self):
        case = load_cases("mixed-rule-balance-operation")[0]
        complete = Mock(return_value={"content": "", "tool_calls": fixture_calls(case)})
        with patch("backend.ToolRegistry.execute", side_effect=AssertionError("不應執行工具")):
            result = asyncio.run(plan_tools(case["question"], case["intents"], complete=complete))
        complete.assert_called_once()
        self.assertEqual(result["model_calls"], 1)
        self.assertTrue(compare(result, case["expected"])["exact_match"])
        _, messages, tools = complete.call_args.args
        self.assertEqual(messages[0]["content"], TASK_TOOL_PROMPT)
        packet = json.loads(messages[-1]["content"])
        self.assertEqual([t["id"] for t in packet["待處理任務"]], ["T1", "T2"])
        self.assertNotIn("expected", packet)
        self.assertNotIn("rationale", packet)
        self.assertNotIn("response_schema", complete.call_args.kwargs)
        definitions = {d["function"]["name"]: d["function"]["parameters"] for d in tools}
        self.assertEqual(definitions["find_rules"]["properties"]["task_id"]["enum"], ["T1"])
        self.assertEqual(definitions["get_my_event_progress"]["properties"]["task_id"]["enum"], ["T2"])
        self.assertNotIn("calculate", definitions)

    def test_no_active_tasks_needs_no_model(self):
        case = load_cases("mixed-no-active")[0]
        complete = Mock(side_effect=AssertionError("無合法業務任務不應呼叫模型"))
        result = asyncio.run(plan_tools(case["question"], case["intents"], complete=complete))
        self.assertEqual(result["model_calls"], 0)
        self.assertTrue(compare(result, case["expected"])["exact_match"])
        complete.assert_not_called()

    def test_forbid_cross_task_and_terminal_call(self):
        case = load_cases("mixed-rule-balance-operation")[0]
        for tid in ("T1", "T3", "T99"):
            with self.subTest(tid=tid):
                result = self.check(case, fixture_calls(case) + [
                    call("get_my_event_progress", task_id=tid, edition="current",
                         account_scope="self", fields=["balance"])])
                self.assertFalse(result["contract_valid"])

    def test_forbid_other_account_and_extra_parameters(self):
        case = load_cases("single-other-account")[0]
        for scope, extra in (("other", {}), ("unclear", {}), ("self", {"player": "B"})):
            with self.subTest(scope=scope, extra=extra):
                result = self.check(case, [
                    call("get_my_event_progress", task_id="T1", edition="current",
                         account_scope=scope, fields=["balance"], **extra)])
                self.assertFalse(result["contract_valid"])
                self.assertEqual(result["tasks"][0]["calls"], [])

    def test_wrong_self_claim_is_detected_by_eval_not_schema(self):
        case = load_cases("single-other-account")[0]
        result = self.check(case, [call("get_my_event_progress", task_id="T1", edition="current",
                                        account_scope="self", fields=["balance"])])
        # 合法 schema 不是語意授權；候選模組不執行任何資料工具。
        self.assertTrue(result["contract_valid"])
        self.assertFalse(compare(result, case["expected"])["exact_match"])

    def test_unsupported_fields_and_duplicate_calls_rejected(self):
        case = load_cases("single-balance")[0]
        good = fixture_calls(case)[0]
        for fields in ([], ["balance", "balance"], ["order_history"]):
            result = self.check(case, [call("get_my_event_progress", task_id="T1",
                                            edition="previous", account_scope="self", fields=fields)])
            self.assertFalse(result["contract_valid"])
        self.assertFalse(self.check(case, [good, good])["contract_valid"])

    def test_missing_task_and_invalid_envelope_are_not_ignored(self):
        case = load_cases("same-balance-editions")[0]
        self.assertFalse(self.check(case, fixture_calls(case)[:1])["contract_valid"])
        self.assertFalse(self.check(case, [{"function": {"name": "bad", "arguments": "{"}}])["contract_valid"])

    def test_item_grounding_is_task_scoped(self):
        case = load_cases("same-two-items")[0]
        result = self.check(case, [
            call("find_shop_item", task_id="T1", edition="current", name="海圖航線框", fields=["price"]),
            fixture_calls(case)[1]])
        self.assertFalse(result["contract_valid"])
        self.assertEqual(result["tasks"][0]["calls"], [])

    def test_dependent_private_query_waits_for_item(self):
        case = load_cases("mixed-gap-and-balance")[0]
        good = fixture_calls(case)
        result = self.check(case, good + [
            call("get_my_event_progress", task_id="T1", edition="current", account_scope="self", fields=["balance"])])
        self.assertFalse(result["contract_valid"])
        self.assertEqual(result["tasks"][0]["calls"], [])
        self.assertEqual(result["tasks"][1]["status"], "planned")

    def test_order_does_not_affect_comparison(self):
        case = load_cases("mixed-compare-and-other")[0]
        result = self.check(case, list(reversed(fixture_calls(case))))
        self.assertTrue(compare(result, case["expected"])["exact_match"])

    def test_omitted_and_extra_tool_calls_fail_exact_match(self):
        case = load_cases("single-item")[0]
        result = self.check(case, [call("find_shop_item", task_id="T1", edition="current",
                                        name="霧港領航員", fields=["price"])])
        self.assertTrue(result["contract_valid"])
        self.assertFalse(compare(result, case["expected"])["exact_match"])

    def test_control_and_business_cannot_mix_for_same_task(self):
        case = load_cases("single-balance")[0]
        result = self.check(case, fixture_calls(case) + [
            call(CONTROL, task_id="T1", status="clarify", reason="不確定")])
        self.assertFalse(result["contract_valid"])
        self.assertEqual(result["tasks"][0]["calls"], [])

    def test_unavailable_edition_not_replaced_by_registry(self):
        case = load_cases("single-unknown-edition")[0]
        result = self.check(case, [call("get_my_event_progress", task_id="T1",
                                        edition="unavailable", account_scope="self", fields=["balance"])])
        self.assertFalse(result["contract_valid"])
