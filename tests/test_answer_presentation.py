"""Player-facing text and verified references are separate presentation data."""
import unittest

from answering import (ANSWER_MARKDOWN_GUIDANCE, ANSWER_SCHEMA, ANSWER_VERIFICATION_FAILED,
                       DIRECT_REPLIES, PART, render_response)
from answer_quality import REVIEW_SCHEMA


def row(label, text, published="2026-11-01"):
    return {"來源": label, "原文": text, "發布日": published}


def supported(question, answer, *citations):
    return {"question": question, "status": "supported", "answer": answer,
            "citations": [{"id": key, "quote": quote} for key, quote in citations]}


class AnswerPresentationTests(unittest.TestCase):
    def render(self, parts, evidence):
        events = []
        result = render_response({"action": "answer", "missing": "", "results": parts},
                                 evidence, lambda title, data: events.append((title, data)))
        return result, dict(events)["Python 檢查回答依據"]

    def test_answer_and_review_use_same_markdown_contract_without_schema_shape_change(self):
        for schema in (ANSWER_SCHEMA, REVIEW_SCHEMA):
            part = schema["properties"]["results"]["items"]
            self.assertIs(part, PART)
            self.assertEqual(set(part["properties"]), {"question", "status", "answer", "citations"})
            self.assertEqual(set(part["required"]), set(part["properties"]))
            self.assertEqual(part["properties"]["answer"]["type"], "string")
            self.assertIn(ANSWER_MARKDOWN_GUIDANCE, part["properties"]["answer"]["description"])
        self.assertIn("每個清單項目各自另起一行", ANSWER_MARKDOWN_GUIDANCE)
        self.assertIn("簡短問答", ANSWER_MARKDOWN_GUIDANCE)

    def test_markdown_and_literal_values_are_preserved_without_rewriting(self):
        cases = [
            "目前有 80 枚。",
            "操作方式：\n\n1. 報名。\n2. 完成對局。\n\n**提醒：** 每日重置。",
            "品項：\n\n- 商品甲：20 枚。\n- 商品乙：35 枚。",
            "**日期及版本**\n\n2026.11.21、3.14、SG-204、UTC+8、23:58",
        ]
        for text in cases:
            with self.subTest(text=text):
                evidence = {"a": row("公告", text)}
                reply, check = self.render([supported("請說明", text, ("a", text))], evidence)
                self.assertEqual(reply, text)
                self.assertEqual(check["子問題"][0]["判定"], "supported")
                self.assertEqual(check["子問題"][0]["採用段落"], [{"id": "a", "quote": text}])

    def test_body_contains_no_generated_source_suffix_and_references_are_deduplicated(self):
        evidence = {
            "a": row("活動公告／玩法", "每日可玩三場。"),
            "b": row("活動公告／兌換", "一百枚可兌換。"),
            "unused": row("額外公告", "無關資訊。"),
        }
        result, check = self.render([
            supported("如何遊玩？", "每日可玩三場。", ("a", "每日可玩三場。")),
            supported("如何兌換？", "一百枚可兌換。", ("a", "每日可玩三場。"), ("b", "一百枚可兌換。")),
        ], evidence)
        self.assertIn("每日可玩三場。", result)
        self.assertIn("一百枚可兌換。", result)
        self.assertNotIn("依據 ·", result)
        self.assertNotIn("活動公告／玩法", result)
        self.assertEqual([ref["來源"] for ref in check["參考資料"]],
                         ["活動公告／玩法", "活動公告／兌換"])
        self.assertEqual(check["參考資料"][0]["段落ID"], ["a"])
        self.assertEqual(check["子問題"][1]["採用段落"][0],
                         {"id": "a", "quote": "每日可玩三場。"})

    def test_multiple_items_from_same_source_share_one_display_reference(self):
        evidence = {"a": row("商城清單", "品項甲", ""), "b": row("商城清單", "品項乙", "")}
        result, check = self.render([
            supported("商品清單", "品項甲及品項乙", ("a", "品項甲"), ("b", "品項乙"))
        ], evidence)
        self.assertEqual(result, "品項甲及品項乙")
        self.assertEqual(check["參考資料"], [
            {"來源": "商城清單", "發布日": "", "段落ID": ["a", "b"]}
        ])

    def test_rejected_part_does_not_expose_its_sources(self):
        evidence = {"a": row("有效來源", "有效內容"), "b": row("錯誤引用來源", "原文")}
        result, check = self.render([
            supported("有效需求", "有效內容", ("a", "有效內容")),
            supported("錯誤需求", "不實回答", ("b", "錯誤摘錄")),
        ], evidence)
        self.assertIn(ANSWER_VERIFICATION_FAILED, result)
        self.assertNotIn("不實回答", result)
        self.assertEqual([ref["來源"] for ref in check["參考資料"]], ["有效來源"])
        self.assertEqual(check["子問題"][1]["採用段落"], [])

    def test_abstention_has_no_references_and_preserves_message(self):
        result, check = self.render([
            {"question": "無資料", "status": "no_evidence", "answer": "", "citations": []}
        ], {"a": row("未採用", "原文")})
        self.assertEqual(result, DIRECT_REPLIES["no_evidence"])
        self.assertEqual(check["參考資料"], [])

    def test_conditional_policy_still_displays_complete_original_clause(self):
        evidence = {"a": {**row("適用公告", "條件成立時，請勿重複送出。"),
                          "適用條件": [{"label": "特定狀態"}]}}
        result, check = self.render([
            supported("處理方式", "請再送出", ("a", "請勿重複送出"))
        ], evidence)
        self.assertEqual(result, "條件成立時，請勿重複送出。")
        self.assertEqual(check["參考資料"][0]["段落ID"], ["a"])


if __name__ == "__main__":
    unittest.main()
