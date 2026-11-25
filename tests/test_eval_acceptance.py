import unittest
from eval_rag import structural_gate


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
