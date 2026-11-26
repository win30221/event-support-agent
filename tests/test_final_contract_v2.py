"""Portable final contract and claim-scoped provenance, without network/model calls."""
import json
import unittest

from answering import ANSWER_VERIFICATION_FAILED, render_response
from evidence import source_alias
from single_output import FINAL_DEFINITION, FINAL_SCHEMA, FinalAnswer, decode_final, final_issues


class FinalContractV2Tests(unittest.TestCase):
    def setUp(self):
        self.evidence = {"price": {"來源": "商品", "原文": "價格 20 枚。"},
                         "rule": {"來源": "公告", "原文": "每日三場。"}}
        self.events = []
        self.observe = lambda title, data: self.events.append((title, data))

    def claim(self, question="價格？", statement="20 枚", condition="", ids=("price",)):
        return {"question": question, "statement": statement, "condition": condition,
                "source_ids": [source_alias(i) for i in ids]}

    def decode(self, payload):
        return decode_final(payload, evidence=self.evidence, original_question="價格？規則？", observe=self.observe)

    def test_portable_schema_has_required_closed_objects_and_no_union(self):
        self.assertEqual(FINAL_SCHEMA["type"], "object")
        self.assertEqual(FINAL_SCHEMA, FINAL_DEFINITION["function"]["parameters"])
        self.assertEqual(set(FINAL_SCHEMA["properties"]), {"supported", "unresolved"})
        def check(node):
            if isinstance(node, dict):
                self.assertFalse(set(node) & {"anyOf", "oneOf", "allOf", "discriminator", "$ref", "$defs"})
                if node.get("type") == "object":
                    self.assertIs(node["additionalProperties"], False)
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for child in node.values():
                    check(child)
            elif isinstance(node, list):
                for child in node:
                    check(child)
        check(FINAL_SCHEMA)
        self.assertEqual(FinalAnswer.model_validate({"supported": [], "unresolved": []}).model_dump(),
                         {"supported": [], "unresolved": []})

    def test_unknown_shape_cannot_carry_prose_or_sources(self):
        for extra in ({"statement": "捏造"}, {"answer": "捏造"}, {"source_ids": [source_alias("price")]}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    self.decode({"supported": [], "unresolved": [{"question": "價格？", "status": "no_evidence", **extra}]})
        with self.assertRaises(ValueError):
            self.decode({"supported": [], "unresolved": []})

    def test_independent_claims_do_not_share_unverified_sources(self):
        payload = {"supported": [self.claim(), self.claim(statement="捏造", ids=("missing",))], "unresolved": []}
        data = self.decode(json.dumps(payload, ensure_ascii=False))
        self.assertEqual([p["status"] for p in data["results"]], ["supported", "rejected"])
        self.assertTrue(final_issues(data, self.evidence))
        text = render_response(data, self.evidence, self.observe)
        self.assertIn("20 枚", text)
        self.assertIn(ANSWER_VERIFICATION_FAILED, text)
        self.assertNotIn("捏造", text)

    def test_condition_must_not_substitute_empty_statement(self):
        data = self.decode({"supported": [self.claim(statement="", condition="每日三場")], "unresolved": []})
        self.assertEqual(data["results"][0]["status"], "rejected")

    def test_conditional_claim_retains_condition_and_all_source_ids(self):
        data = self.decode({"supported": [self.claim(condition="每日三場", ids=("price", "rule"))], "unresolved": []})
        self.assertFalse(final_issues(data, self.evidence))
        text = render_response(data, self.evidence, self.observe)
        self.assertIn("成立條件", text)
        self.assertIn("每日三場", text)
        self.assertEqual([c["id"] for c in data["results"][0]["citations"]], ["price", "rule"])

    def test_legacy_compatibility_does_not_accept_unverified_abstention_body(self):
        data = self.decode({"results": [{"question": "價格？", "status": "no_evidence",
                                        "answer": "捏造", "source_ids": [source_alias("price")]}]})
        self.assertEqual(data["results"][0]["status"], "rejected")
        self.assertIn(("Python 終止契約版本", {"version": 1, "claims": None}), self.events)
