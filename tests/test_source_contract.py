"""Regression of the observed real-model failure, plus current-turn trust boundaries."""
import json
import unittest

from answering import ANSWER_SCHEMA, ANSWER_VERIFICATION_FAILED, decode_response, render_response
from evidence import model_sources, source_alias
from guide import CATALOG, published_passages


class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.rows = {r["段落ID"]: r for r in published_passages(CATALOG.current)}
        self.key = f"{CATALOG.current}/launch/玩法"
        self.question = "新手怎麼玩這個活動？"

    def part(self, ids, answer="每天前 3 場冒險各得 20 枚星星幣。", **kwargs):
        return {"question": self.question, "status": "supported", "answer": answer,
                "source_ids": ids, **kwargs}

    def render(self, *parts, evidence=None):
        evidence = self.rows if evidence is None else evidence
        events = []
        data = decode_response({"action": "answer", "missing": "", "results": list(parts)},
                               evidence=evidence, original_question=self.question)
        text = render_response(data, evidence, lambda t, d: events.append((t, d)))
        return text, dict(events)["Python 檢查回答依據"]

    def test_observed_failure_now_needs_only_source_ids(self):
        # Real trace: five source IDs existed; five paraphrased quotes failed.
        keys = [f"{CATALOG.current}/{suffix}" for suffix in (
            "launch/參加", "launch/玩法", "launch/兌換", "match/練習與掃蕩", "match/組隊各自計次")]
        text, checks = self.render(self.part([source_alias(k) for k in keys]))
        self.assertNotIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertEqual(checks["判定"], "supported")
        adopted = checks["子問題"][0]["採用段落"]
        self.assertEqual([c["id"] for c in adopted], keys)
        self.assertTrue(all(c["quote"] == self.rows[c["id"]]["原文"] for c in adopted))

    def test_unknown_id_does_not_launder_answer_or_discard_other_part(self):
        good = self.part([source_alias(self.key), source_alias(self.key)])
        bad = self.part([source_alias(self.key), "Eunknown"], answer="未驗證內容")
        bad["question"] = "另一個需求"
        text, checks = self.render(good, bad)
        self.assertIn("20 枚", text)
        self.assertIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertNotIn("未驗證內容", text)
        self.assertEqual([p["判定"] for p in checks["子問題"]], ["supported", "rejected"])
        self.assertEqual(len(checks["子問題"][0]["採用段落"]), 1)

    def test_invalid_part_shape_does_not_discard_valid_part(self):
        text, checks = self.render(self.part([source_alias(self.key)]),
                                  {"question": "其他需求", "status": "supported", "source_ids": [None]})
        self.assertIn("20 枚", text)
        self.assertIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertEqual(checks["子問題"][1]["判定"], "rejected")

    def test_missing_current_turn_id_is_rejected(self):
        text, checks = self.render(self.part([source_alias(self.key)]), evidence={})
        self.assertEqual(text, ANSWER_VERIFICATION_FAILED)
        self.assertEqual(checks["參考資料"], [])

    def test_model_projection_preserves_required_conditions_without_duplicate_raw_fields(self):
        row = {**self.rows[self.key], "適用條件": [{"label": "condition", "mentions_any": ["code"]}]}
        groups = [{"工具": "find_rules", "狀態": "found", "段落": [row]},
                  {"工具": "find_rules", "狀態": "found", "段落": [row]}]
        result = model_sources(groups, aliases=True)
        first = result[0]["段落"][0]
        self.assertEqual(first["段落ID"], source_alias(self.key))
        self.assertEqual(first["原文"], row["原文"])
        self.assertEqual(first["適用條件"], row["適用條件"])
        self.assertNotIn("文件ID", first)
        self.assertEqual(result[1]["段落"], [])
        self.assertEqual(result[1]["已回傳段落ID"], [source_alias(self.key)])
        self.assertEqual(groups[0]["段落"][0]["段落ID"], self.key)

    def test_schema_never_requests_model_quotes(self):
        self.assertNotIn('"quote"', json.dumps(ANSWER_SCHEMA))
