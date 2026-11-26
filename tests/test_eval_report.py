"""Evaluation V2 regressions: no LLM, network, or database."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from evaluations.eval_report import (CRITERIA, RUBRIC, grade_report, grade_turn, group_result,
                                      load_json, review_template, validate_rubric, write_new)
from transport_observation import capture_transport, record_transport


def event(title, data):
    return {"title": title, "data": data}


def result(name, data, error=False):
    return event("收到工具回傳 · " + name, {"isError": error, "結構化資料": data})


def dispatch(name, edition):
    return event("呼叫工具 · " + name, {"edition": edition})


def turn(question="商品資訊？", *, adopted=(), status="supported", events=None, tools=()):
    return {"question": question, "reply": "fixture answer",
            "events": events if events is not None else [event("Python 單一 Agent", {})],
            "tools": list(tools), "checks": [{"子問題": [{"判定": status,
                "採用段落": [{"id": i} for i in adopted]}]}], "metrics": {}}


def rule(groups=(), *, editions=("2026-11",), forbidden=(), required=(), statuses=("supported",)):
    return {"question": "商品資訊？", "reference": "核對指定事實", "editions": list(editions),
            "expected_statuses": list(statuses), "required_tools": list(required),
            "forbidden_tools": list(forbidden), "evidence_groups": list(groups)}


def group(*options):
    return {"fact": "必要事實", "any_of": [list(o) for o in options]}


class EvalReportTests(unittest.TestCase):
    def test_and_or_evidence_groups(self):
        groups = [group(("a", "b"), ("c",)), group(("d",))]
        self.assertEqual([r["covered"] for r in group_result(groups, {"a", "d"})], [False, True])
        self.assertEqual([r["covered"] for r in group_result(groups, {"c", "d"})], [True, True])

    def test_rubric_matches_frozen_suites(self):
        rubric = load_json(RUBRIC)
        validate_rubric(rubric)
        root = RUBRIC.parent
        cases = load_json(root / "rag_cases.json") + load_json(root / "task_cases.json")
        holdout = load_json(root / "holdout_cases.json")
        self.assertEqual(len(cases), 31)
        self.assertEqual(len(holdout), 9)
        for suite, rows in (("baseline", cases), ("holdout", holdout)):
            by_id = {r["id"]: r for r in rubric["cases"] if r["suite"] == suite}
            self.assertEqual(set(by_id), {c["id"] for c in rows})
            for case in rows:
                r = by_id[case["id"]]
                self.assertEqual(r["player"], case.get("player", "A"))
                self.assertEqual(r["legacy_sources"], case["sources"])
                self.assertEqual([t["question"] for t in r["turns"]], [*case.get("before", []), case["question"]])
                self.assertEqual(r["turns"][-1]["reference"], case["reference"])
                self.assertEqual(r["turns"][-1]["editions"], case["editions"])

    def test_retrieval_diagnostics_unknown_and_observed(self):
        from evaluations.eval_report import retrieval_summary
        old = grade_turn(turn(), rule())
        self.assertIsNone(old["efficiency"]["retrieval_diagnostics"])
        self.assertIsNone(retrieval_summary([old])["observed_cache"])
        counts = {"requests": 3, "computations": 2, "reuses": 1, "failures": 0}
        events = [
            event("Python 檢索快取統計", {"version": 1, "ranking": counts, "query_vector": counts}),
            event("Python 查詢進展", {"version": 1, "rule_queries": 1, "new_rule_ids": 0}),
            event("Python 查詢進展", {"version": 1, "rule_queries": 0, "new_rule_ids": 0}),
            event("Python 單一 Agent 輪次", {"模型呼叫": 3, "停止資料工具原因": ["terminal_repair"]}),
            event("Python 單一 Agent 回傳驗證失敗", {"錯誤代碼": "INVALID_FINAL_SCHEMA"}),
        ]
        new = grade_turn(turn(events=events), rule())
        summary = retrieval_summary([old, new])
        self.assertEqual(summary["observed_turns"], 1)
        self.assertEqual(summary["unknown_turns"], 1)
        self.assertEqual(summary["observed_cache"]["query_vector"], counts)
        self.assertEqual(summary["observed_rule_batches_without_new_ids"], 1)
        self.assertEqual(summary["observed_schema_reasons"], {"terminal_repair": 1})
        self.assertEqual(summary["observed_repair_codes"], {"INVALID_FINAL_SCHEMA": 1})

    def test_empty_alternative_rejected(self):
        rubric = {"version": "2.0.0", "cases": [{"id": "a", "turns": [rule([group(())])]}]}
        with self.assertRaises(ValueError):
            validate_rubric(rubric)

    def test_catalogue_alternative_and_irrelevant_public_scope(self):
        sku = "2026-11/shop-items/example"
        doc = "2026-11/doc/item"
        events = [dispatch("find_rules", "2026-10"), dispatch("find_shop_item", "2026-11"),
                  result("find_shop_item", {"edition": "2026-11", "status": "found", "items": [{"id": "example"}]})]
        row = grade_turn(turn(adopted=[sku], events=events), rule([group((doc,), (sku,))]))
        self.assertEqual(row["evidence"]["status"], "pass")
        self.assertEqual(row["contract"]["status"], "pass")
        self.assertEqual(len(row["efficiency"]["extra_public_dispatches"]), 1)
        self.assertTrue(all(v == "not_run" for v in row["semantic"].values()))
        self.assertIsNone(row["reliability"]["transport"])

    def test_incomplete_or_fabricated_alternative_not_passed(self):
        doc, sku = "2026-11/doc/item", "2026-11/shop-items/example"
        events = [result("find_shop_item", {"edition": "2026-11", "status": "found", "items": [{"id": "example"}]})]
        for r, adopted in ((rule([group((doc,))]), [sku]), (rule([group((doc,), (sku,))]), [doc])):
            self.assertEqual(grade_turn(turn(adopted=adopted, events=events), r)["evidence"]["status"], "fail")

    def test_withheld_or_failed_results_not_usable(self):
        doc = "2026-11/doc/item"
        for events in ([result("find_rules", {"result": [{"段落ID": doc}]}, error=True)],
                       [result("find_rules", {"result": [{"段落ID": doc}]}),
                        event("Python 核對公告適用條件", {"未採用段落": [{"段落ID": doc}]})]):
            self.assertEqual(grade_turn(turn(adopted=[doc], events=events), rule([group((doc,))]))["evidence"]["status"], "fail")

    def test_wrong_edition_used_is_contract_failure(self):
        row = grade_turn(turn(adopted=["2026-10/doc/item"]), rule())
        self.assertEqual(row["contract"]["status"], "fail")

    def test_proposal_is_not_dispatch(self):
        proposal = event("收到模型回傳 · 單一 Agent", {"tool_calls": [{"function": {
            "name": "get_my_event_progress", "arguments": json.dumps({"account_scope": "other"})}}]})
        r = rule(editions=(), forbidden=("get_my_event_progress",), statuses=("account_limit",))
        row = grade_turn(turn(status="account_limit", events=[proposal]), r)
        self.assertEqual(row["safety"]["status"], "pass")
        self.assertEqual(row["safety"]["proposals_status"], "fail")
        row = grade_turn(turn(status="account_limit", events=[proposal, dispatch("get_my_event_progress", "2026-11")]), r)
        self.assertEqual(row["safety"]["status"], "fail")

    def test_answer_rejection_and_task_failure_remain_visible(self):
        row = grade_turn(turn(status="rejected"), rule(required=["get_my_event_progress"]))
        self.assertEqual(row["contract"]["status"], "fail")
        self.assertEqual(row["contract"]["task_status"], "fail")
        self.assertEqual(len(row["contract"]["task_issues"]), 2)

    def test_report_review_and_partial_multiturn(self):
        r = rule()
        rubric = {"version": "2.0.0", "cases": [{"id": "case", "player": "A",
                  "legacy_sources": [], "turns": [r, r]}]}
        report = {"run": {}, "expected_trials": 1, "completed_trials": 1, "complete": True,
                  "cases": [{"case": "case", "trial": 1, "player": "A",
                             "reference": r["reference"], "expected_editions": r["editions"],
                             "gate_issues": ["unexpected edition"], "execution_error": None,
                             "turns": [turn()]}]}
        partial = grade_report(report, rubric)
        self.assertEqual(partial["summary"]["execution_failures"], 1)
        report["cases"][0]["turns"].append(turn())
        grade = grade_report(report, rubric)
        self.assertEqual(grade["summary"]["legacy_fail"], 1)
        self.assertEqual(grade["summary"]["turns"], 2)
        review = review_template(grade)
        review["reviewer"] = "human"
        review["turns"][0]["criteria"]["correctness"] = "fail"
        review["turns"][0]["notes"] = "Unsupported inference"
        reviewed = grade_report(report, rubric, review)
        self.assertEqual(reviewed["summary"]["semantic_review"]["correctness"], {"fail": 1, "not_run": 1})
        changed = copy.deepcopy(report)
        changed["cases"][0]["turns"][0]["reply"] = "different"
        with self.assertRaises(ValueError):
            grade_report(changed, rubric, review)

    def test_report_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            write_new(path, {"original": True})
            with self.assertRaises(FileExistsError):
                write_new(path, {"original": False})
            self.assertEqual(load_json(path), {"original": True})


class TransportObservationTests(unittest.TestCase):
    def test_context_propagates_to_parallel_threads_and_resets(self):
        async def capture():
            with capture_transport() as data:
                await asyncio.gather(*[asyncio.to_thread(record_transport, "nvidia", "embedding",
                                                        "attempt", attempt=i) for i in range(4)])
            record_transport("nvidia", "chat", "attempt")
            return data
        data = asyncio.run(capture())
        self.assertEqual(sorted(e["attempt"] for e in data["events"]), list(range(4)))
        with capture_transport() as next_turn:
            record_transport("nvidia", "chat", "failure", http_status=429)
        self.assertEqual(len(next_turn["events"]), 1)
        self.assertEqual(next_turn["events"][0]["http_status"], 429)
