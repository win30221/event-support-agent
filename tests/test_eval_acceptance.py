from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from embedding_client import TransientEmbeddingError
from evaluations.eval_rag import benchmark, save_report, structural_gate, turn_metrics


class AcceptanceTests(unittest.TestCase):
    def test_answerable_refusal_fails_even_without_required_paragraphs(self):
        case = {"mode": "progress", "id": "balance", "editions": ["2026-11"], "progress": True}
        turn = {"reply": "沒有資料", "checks": [{"子問題": [{"判定": "no_evidence"}]}], "tools": []}
        coverage = {"missing_retrieval": [], "missing_answer": []}
        self.assertIn("answerable case not supported", structural_gate(case, [turn], coverage))

    def test_account_boundary_fails_on_private_lookup(self):
        case = {"mode": "outside-account", "id": "other", "editions": []}
        turn = {"reply": "不能查", "checks": [{"子問題": [{"判定": "account_limit"}]}],
                "tools": [{"name": "get_my_event_progress", "arguments": {"edition": "2026-11"}}]}
        coverage = {"missing_retrieval": [], "missing_answer": []}
        self.assertIn("private lookup outside account", structural_gate(case, [turn], coverage))

    def test_report_checkpoint_distinguishes_partial_and_complete_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            run = {"provider": "nvidia"}
            details = [{"case": "a", "gate_issues": []}]
            save_report(path, run, details, 2)
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(report["complete"])
            self.assertEqual(report["completed_trials"], 1)
            details.append({"case": "b", "gate_issues": ["failure"]})
            save_report(path, run, details, 2)
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(report["complete"])
            self.assertEqual(report["cases"], details)

    def test_model_and_tool_latency_from_observed_events(self):
        events = [
            ("Python 模型請求指標 · 單一 Agent", {"耗時毫秒": 123.5}),
            ("Python 模型請求指標 · 單一 Agent", {"耗時毫秒": 234.5}),
            ("Python 工具執行指標", {"耗時毫秒": 2.25}),
        ]
        metrics = turn_metrics(events, [], 0.5)
        self.assertEqual(metrics["model_request_ms"], 358.0)
        self.assertEqual(metrics["tool_execution_ms"], 2.25)
        self.assertIsNone(turn_metrics([], [], 0.5)["model_request_ms"])

    def test_hybrid_service_failure_is_not_a_retrieval_miss(self):
        cases = [{"id": "a", "sources": ["doc"], "mode": "search"},
                 {"id": "b", "sources": ["doc"], "mode": "search"}]
        baseline = ({"doc"}, {"doc"}, {"doc"}, set())
        output = io.StringIO()
        with patch("evaluations.eval_rag.offline", return_value=baseline):
            with patch("evaluations.eval_rag.offline_hybrid",
                       side_effect=[TransientEmbeddingError("nvidia embedding HTTP 500 after 3 attempt(s)."), {"doc"}]):
                with redirect_stdout(output), self.assertRaises(SystemExit) as exit:
                    benchmark(cases, hybrid=True)
        self.assertEqual(exit.exception.code, 1)
        self.assertIn("Hybrid 完成案例：1/2", output.getvalue())
        self.assertIn("成功完成的有標記案例）：必要段落找齊 1/1", output.getvalue())
        self.assertIn("Hybrid 執行失敗", output.getvalue())
        self.assertIn("失敗案例：a", output.getvalue())
