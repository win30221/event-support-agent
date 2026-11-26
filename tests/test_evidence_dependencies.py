"""Current-turn provenance closure and terminal-answer regression."""
import unittest
from copy import deepcopy

from answering import ANSWER_VERIFICATION_FAILED, decode_response, render_response
from evidence import expand_evidence_dependencies, source_alias
from single_output import decode_final


def row(identifier, original, dependencies=None, **extra):
    value = {"段落ID": identifier, "來源": identifier, "原文": original, **extra}
    if dependencies is not None:
        value["運算依據"] = dependencies
    return value


def part(question, ids, answer="計算結果為 20。", status="supported"):
    return {"question": question, "status": status, "answer": answer,
            "source_ids": [source_alias(i) for i in ids]}


class EvidenceDependencyTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {
            "price": row("price", "價格為 100。"),
            "balance": row("balance", "餘額為 80。"),
            "calculation/1": row("calculation/1", "100 - 80 = 20", ["price", "balance"]),
            "calculation/2": row("calculation/2", "20 + 80 = 100", ["calculation/1", "balance"]),
            "unrelated": row("unrelated", "不相關資料。"),
        }

    def render(self, *parts, legacy=False):
        events = []
        def observe(title, data):
            events.append((title, data))
        if legacy:
            data = decode_response({"action": "answer", "missing": "", "results": list(parts)},
                                   evidence=self.evidence, original_question="測試需求", observe=observe)
        else:
            data = decode_final({"results": list(parts)}, evidence=self.evidence,
                                original_question="測試需求", observe=observe)
        text = render_response(data, self.evidence, observe)
        return text, dict(events)["Python 檢查回答依據"], events

    def test_nested_shared_dependencies_and_stable_deduplication(self):
        before = deepcopy(self.evidence)
        result = expand_evidence_dependencies(
            ["calculation/2", "balance", "calculation/2"], self.evidence)
        self.assertEqual(result, ["calculation/2", "calculation/1", "price", "balance"])
        self.assertEqual(self.evidence, before)

    def test_only_computed_id_adopts_originals(self):
        text, check, events = self.render(part("計算", ["calculation/1"]))
        self.assertEqual(text, "計算結果為 20。")
        self.assertEqual(check["判定"], "supported")
        adopted = check["子問題"][0]["採用段落"]
        self.assertEqual([c["id"] for c in adopted], ["calculation/1", "price", "balance"])
        self.assertTrue(all(c["quote"] == self.evidence[c["id"]]["原文"] for c in adopted))
        self.assertEqual(dict(events)["Python 展開證據依賴"]["主機補齊"], ["price", "balance"])
        self.assertEqual([r["來源"] for r in check["參考資料"]], ["calculation/1", "price", "balance"])

    def test_explicit_all_references_remain_valid(self):
        text, check, events = self.render(part("計算", ["price", "balance", "calculation/1"]))
        self.assertEqual(text, "計算結果為 20。")
        self.assertEqual(len(check["子問題"][0]["採用段落"]), 3)
        self.assertNotIn("Python 展開證據依賴", dict(events))

    def test_missing_dependency_rejects_only_affected_part(self):
        del self.evidence["price"]
        text, check, _ = self.render(part("計算", ["calculation/1"]),
                                     part("餘額", ["balance"], "餘額為 80。"))
        self.assertIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertIn("餘額為 80。", text)
        self.assertEqual([p["判定"] for p in check["子問題"]], ["rejected", "supported"])
        self.assertEqual([r["來源"] for r in check["參考資料"]], ["balance"])

    def test_explicit_unknown_id_is_not_dropped(self):
        text, check, _ = self.render(part("計算", ["calculation/1", "old-turn"]))
        self.assertEqual(text, ANSWER_VERIFICATION_FAILED)
        self.assertEqual(check["參考資料"], [])

    def test_cycles_and_malformed_edges_fail_closed(self):
        for edges in (["calculation/1"], ["calculation/2"], [], "price", [None], [""], ["old-turn"]):
            with self.subTest(edges=edges):
                self.evidence["calculation/1"]["運算依據"] = edges
                text, check, _ = self.render(part("計算", ["calculation/1"]))
                self.assertEqual(text, ANSWER_VERIFICATION_FAILED)
                self.assertEqual(check["參考資料"], [])

    def test_missing_dependency_original_is_rejected(self):
        del self.evidence["price"]["原文"]
        text, check, _ = self.render(part("計算", ["calculation/1"]))
        self.assertEqual(text, ANSWER_VERIFICATION_FAILED)
        self.assertEqual(check["參考資料"], [])

    def test_negative_lookup_dependency_is_not_laundered(self):
        self.evidence["price"]["資料狀態"] = "not_found"
        text, check, _ = self.render(part("計算", ["calculation/1"]))
        self.assertEqual(text, ANSWER_VERIFICATION_FAILED)
        self.assertEqual(check["參考資料"], [])

    def test_conditional_dependency_preserves_original_guard(self):
        self.evidence["price"]["適用條件"] = [{"label": "需核對"}]
        text, check, _ = self.render(part("計算", ["calculation/1"], "未經核對的操作"))
        self.assertNotIn("未經核對的操作", text)
        self.assertIn("價格為 100。", text)
        self.assertEqual(check["判定"], "supported")

    def test_no_evidence_does_not_expand_or_publish_dependencies(self):
        text, check, events = self.render(part("不確定", ["calculation/1"], "", "no_evidence"))
        self.assertEqual(check["參考資料"], [])
        self.assertNotIn("Python 展開證據依賴", dict(events))

    def test_legacy_contract_still_requires_explicit_dependencies(self):
        text, check, _ = self.render(part("計算", ["calculation/1"]), legacy=True)
        self.assertEqual(text, ANSWER_VERIFICATION_FAILED)
        self.assertEqual(check["參考資料"], [])
