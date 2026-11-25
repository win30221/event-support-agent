"""Regression coverage for negative facts misclassified as no_evidence.

Real local lookup results, scripted model outputs, no provider/network dependency.
The recovered assertion is host-owned; no arbitrary refusal prose is accepted.
"""
import asyncio
import json
import os
from copy import deepcopy
from unittest.mock import patch

from agent import legacy_answer as answer
from answer_quality import validate_review
from answering import DIRECT_REPLIES, decode_response, render_response
from backend import ToolRegistry, evidence_for
from guide import CATALOG, shop_catalog
from request_context import RequestContext
from tests.test_bounded_agent import (abstain, call, evidence_rows, select, original, response,
                                rules, scripted, supported, fixture_id)
from tests.test_guide import OfflineTests

CURRENT, PREVIOUS = CATALOG.current, CATALOG.previous


def mislabeled(question, *rows, status="no_evidence", prose="不應信任模型文字：已扣款。"):
    return {"question": question, "status": status, "answer": prose,
            "source_ids": [fixture_id(row["段落ID"]) for row in rows]}


def lookup(name, edition=CURRENT):
    registry = ToolRegistry("A")
    question = f"我想兌換{name}，怎麼失敗？"
    args = registry.validate("find_shop_item", {"edition": edition, "name": name},
                             RequestContext.build(question, []), {})
    result = registry.execute("find_shop_item", args)
    source, _, _ = evidence_for("find_shop_item", args, result, question)
    return source["段落"]


class VerifiedLookupTests(OfflineTests):
    def setUp(self):
        super().setUp()
        config = patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_ANSWER_REVIEW": "auto"})
        config.start()
        self.addCleanup(config.stop)

    def normalize(self, part, evidence, question=None):
        events = []
        data = decode_response({"action": "answer", "missing": "", "results": [deepcopy(part)]},
                               evidence=evidence, original_question=question or part["question"],
                               observe=lambda title, data: events.append((title, data)))
        text = render_response(data, evidence, lambda title, data: events.append((title, data)))
        return data["results"][0], text, dict(events)

    def ask(self, question, *replies, history=()):
        events = []
        model = scripted(*replies)
        text, trace = asyncio.run(answer("A", "ollama", list(history), question, model,
                                         lambda title, data: events.append((title, data))))
        self.assertEqual(model.call_count, len(replies))
        return text, trace, dict(events)

    def test_observed_four_turn_trace_with_mislabeled_two_edition_negative_evidence(self):
        question = "那我想兌換特斯拉怎麼失敗?"
        history = [
            {"role": "user", "content": "這個活動要怎麼玩?"},
            {"role": "assistant", "content": "介紹活動玩法及披風。", "trace": []},
            {"role": "user", "content": "其他的商品價格多少?"},
            {"role": "assistant", "content": "其他商品價格。", "trace": []},
            {"role": "user", "content": "那我現在有多少星星幣？"},
            {"role": "assistant", "content": "您目前擁有80枚星星幣。",
             "trace": [{"工具": "get_my_event_progress", "參數": {"edition": CURRENT}}]},
        ]

        def finish(provider, messages, tools, **kwargs):
            rows = list(evidence_rows(messages).values())
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["資料狀態"] == "not_found" for row in rows))
            return response(mislabeled(question, *rows, prose="特斯拉不存在；擅自聲稱已扣款。"))

        text, trace, events = self.ask(question, select(
            call("find_shop_item", edition=CURRENT, name="特斯拉"),
            call("find_shop_item", edition=PREVIOUS, name="特斯拉")), finish, history=history)
        self.assertEqual(len(trace), 2)
        self.assertIn("十一月星夜祭", text)
        self.assertIn("十月星夜祭", text)
        self.assertIn("商城清單查無「特斯拉」", text)
        self.assertIn("請確認商品名稱", text)
        self.assertNotIn("已扣款", text)
        self.assertNotIn(DIRECT_REPLIES["no_evidence"], text)
        self.assertIn("Python 正規化查詢狀態", events)
        self.assertNotIn("Python 啟動回答品質審查", events)
        check = events["Python 檢查回答依據"]
        self.assertEqual(check["判定"], "supported")
        self.assertEqual(len(check["參考資料"]), 2)
        self.assertEqual(len(check["子問題"][0]["採用段落"]), 2)

    def test_generic_negative_fact_for_different_names_and_wording(self):
        for name, question in (
            ("銀河戰車", "銀河戰車可以換嗎？"),
            ("ZXQ-92841", "請問有販售ZXQ-92841嗎？"),
            ("翡翠王冠", "找不到翡翠王冠，能幫忙確認嗎？"),
        ):
            with self.subTest(name=name):
                row = lookup(name)[0]
                part, text, events = self.normalize(mislabeled(question, row), {row["段落ID"]: row})
                self.assertEqual(part["status"], "supported")
                self.assertIn(row["原文"], text)
                self.assertNotIn("已扣款", text)
                self.assertEqual(len(events["Python 檢查回答依據"]["參考資料"]), 1)

    def test_invalid_stale_partial_and_mixed_citations_remain_abstentions(self):
        name, question = "銀河戰車", "我想兌換銀河戰車"
        row = lookup(name)[0]
        rule = {"段落ID": "rule", "來源": "公告", "原文": "每日三場。"}
        evidence = {row["段落ID"]: row, "rule": rule}
        good = mislabeled(question, row)
        unknown = deepcopy(good)
        unknown["source_ids"][0] = "previous-turn-or-invented"
        partial = deepcopy(good)
        partial["source_ids"].append("unknown")
        incomplete = deepcopy(good)
        incomplete["source_ids"][0] = incomplete["source_ids"][0][:-1]
        malformed = deepcopy(good)
        malformed["source_ids"] = [None]
        mixed = mislabeled(question, row, rule)
        empty = deepcopy(good)
        empty["source_ids"] = []
        for case in (unknown, partial, incomplete, malformed, mixed, empty):
            with self.subTest(case=case):
                part, text, events = self.normalize(case, evidence)
                self.assertEqual(part["status"], "no_evidence")
                self.assertEqual(text, DIRECT_REPLIES["no_evidence"])
                self.assertEqual(events["Python 檢查回答依據"]["參考資料"], [])
                self.assertNotIn("Python 正規化查詢狀態", events)

    def test_unrelated_or_only_implicit_reference_is_not_auto_promoted(self):
        row = lookup("銀河戰車")[0]
        evidence = {row["段落ID"]: row}
        cases = [
            ("我有多少星星幣？", "銀河戰車可換嗎？"),
            ("銀河戰車可以換嗎？我的餘額呢？", "我的餘額"),
            ("那它怎麼換不到？", "銀河戰車"),
        ]
        for original_question, label in cases:
            with self.subTest(original_question=original_question, label=label):
                part, text, events = self.normalize(mislabeled(label, row), evidence, original_question)
                self.assertEqual(part["status"], "no_evidence")
                self.assertEqual(text, DIRECT_REPLIES["no_evidence"])

    def test_other_lookup_states_are_not_negative_facts(self):
        name, question = "銀河戰車", "銀河戰車可以換嗎？"
        for status in ("unavailable", "ambiguous", "need_name", "listed"):
            with self.subTest(status=status):
                source, _, _ = evidence_for("find_shop_item", {"edition": CURRENT, "name": name},
                    {"edition": CURRENT, "title": "測試商城", "status": status, "items": []}, question)
                row = source["段落"][-1]
                part, text, events = self.normalize(mislabeled(question, row), {row["段落ID"]: row})
                self.assertEqual(part["status"], "no_evidence")
                self.assertEqual(text, DIRECT_REPLIES["no_evidence"])
                self.assertNotIn("Python 正規化查詢狀態", events)

    def test_protected_and_clarification_statuses_are_not_overridden(self):
        question = "銀河戰車以及其他帳號的資料"
        row = lookup("銀河戰車")[0]
        for status in ("account_limit", "service_help", "clarify", "unavailable"):
            with self.subTest(status=status):
                part, text, events = self.normalize(mislabeled(question, row, status=status),
                                                     {row["段落ID"]: row})
                self.assertEqual(part["status"], status)
                self.assertEqual(text, DIRECT_REPLIES[status])
                self.assertEqual(events["Python 檢查回答依據"]["參考資料"], [])

    def test_independent_balance_and_negative_lookup_survive_together(self):
        name, question = "銀河戰車", "銀河戰車怎麼換不到？另外我的餘額有多少？"
        def finish(provider, messages, tools, **kwargs):
            rows = evidence_rows(messages)
            missing = next(row for row in rows.values() if row.get("資料狀態") == "not_found")
            return response(mislabeled("銀河戰車怎麼換不到？", missing),
                            supported("我的餘額", "80 枚星星幣",
                                      (f"{CURRENT}/my-progress", '"星星幣": 80')))

        text, trace, events = self.ask(question, select(
            call("find_shop_item", edition="current", name=name),
            call("get_my_event_progress", edition="current")), finish)
        self.assertEqual(len(trace), 2)
        self.assertIn("商城清單查無「銀河戰車」", text)
        self.assertIn("80 枚星星幣", text)
        self.assertEqual([p["判定"] for p in events["Python 檢查回答依據"]["子問題"]],
                         ["supported", "supported"])

    def test_local_lookup_error_cannot_be_recovered_as_absence(self):
        name, question = "銀河戰車", "銀河戰車怎麼換不到？"
        stale = lookup(name)[0]
        def unavailable(*args, **kwargs):
            raise RuntimeError("database unavailable")
        with patch.object(ToolRegistry, "execute", unavailable):
            text, trace, events = self.ask(question,
                select(call("find_shop_item", edition="current", name=name)),
                response(mislabeled(question, stale)),
                select(), response(mislabeled(question, stale)))
        self.assertEqual(trace, [])
        self.assertEqual(text, DIRECT_REPLIES["unavailable"])
        self.assertNotIn("商城清單查無", text)
        self.assertNotIn("Python 正規化查詢狀態", events)

    def test_existing_supported_negative_fact_unchanged_and_safe(self):
        row = lookup("銀河戰車")[0]
        part, text, events = self.normalize(mislabeled("銀河戰車可以換嗎？", row, status="supported"),
                                            {row["段落ID"]: row})
        self.assertEqual(part["status"], "supported")
        self.assertIn(row["原文"], text)
        self.assertNotIn("已扣款", text)
        self.assertNotIn("Python 正規化查詢狀態", events)

    def test_review_uses_same_normalization_without_relaxing_account_protection(self):
        name, question = "銀河戰車", "銀河戰車可以換嗎？還想查其他人的餘額"
        label = "銀河戰車可以換嗎？"
        row = lookup(name)[0]
        evidence = {row["段落ID"]: row}
        original_data = decode_response({"action": "answer", "missing": "", "results": [
            supported(label, row["原文"], (row["段落ID"], row["原文"])),
            abstain("其他人的餘額", "account_limit"),
        ]}, evidence=evidence, original_question=question)
        draft = {"decision": "revise", "reason": "正規化查無結果", "missing_ids": [],
                 "results": [mislabeled(label, row), abstain("其他人的餘額", "account_limit")]}
        revised, _, _ = validate_review(deepcopy(draft), original_data, evidence,
                                          lambda *_: None, original_question=question)
        self.assertEqual([part["status"] for part in revised["results"]],
                         ["supported", "account_limit"])
        draft["results"].pop()
        with self.assertRaises(ValueError):
            validate_review(draft, original_data, evidence, lambda *_: None,
                            original_question=question)
