"""Host limitation rendering regressions; no model calls or database required."""
import unittest

from answer_limitations import build_limitations


def source(name, status, **extra):
    return {"工具": name, "狀態": status, **extra}


class LimitationContextTests(unittest.TestCase):
    def test_no_query_and_invalid_proposal_are_not_execution(self):
        for errors in ([], [source("get_my_event_progress", "invalid_arguments", 原因="SECRET")]):
            context = build_limitations([], errors)
            self.assertEqual(context.codes, ("no_observed_data_context",))
            self.assertEqual(context.summary(), "")

    def test_search_states_are_distinct_and_never_global_absence(self):
        expected = {"found": "rules_found", "context_only": "rules_context_only", "no_match": "rules_no_match"}
        for status, code in expected.items():
            with self.subTest(status=status):
                context = build_limitations([source("find_rules", status, 段落=[{"原文": "SECRET"}])], [])
                self.assertEqual(context.codes, (code,))
                self.assertIn("本輪", context.summary())
                self.assertNotIn("SECRET", context.summary())
        self.assertIn("不代表相關規則不存在", build_limitations([source("find_rules", "no_match")], []).summary())

    def test_shop_states_do_not_become_a_policy_or_global_absence(self):
        expected = {"found": "catalogue_scope", "listed": "catalogue_scope",
                    "not_found": "catalogue_not_found", "ambiguous": "catalogue_ambiguous",
                    "unavailable": "catalogue_unavailable", "need_name": "catalogue_need_name"}
        for status, code in expected.items():
            with self.subTest(status=status):
                context = build_limitations([source("find_shop_item", status, 商城={"name": "SECRET"})], [])
                self.assertEqual(context.codes, (code,))
                self.assertNotIn("SECRET", context.summary())
        self.assertIn("品名及檔期", build_limitations([source("find_shop_item", "not_found")], []).summary())

    def test_progress_scope_does_not_disclose_record_values(self):
        context = build_limitations([source("get_my_event_progress", "found", 本人紀錄={"星星幣": 987654})], [])
        self.assertEqual(context.codes, ("progress_scope",))
        self.assertIn("不包含累計遊玩天數", context.summary())
        self.assertNotIn("987654", context.summary())
        unavailable = build_limitations([source("get_my_event_progress", "unavailable")], [])
        self.assertEqual(unavailable.codes, ("progress_unavailable",))
        self.assertNotIn("progress_scope", unavailable.codes)

    def test_actual_execution_error_does_not_expose_details(self):
        context = build_limitations([], [source("find_rules", "tool_error", 原因="SECRET_DSN")])
        self.assertEqual(context.codes, ("tool_error",))
        self.assertIn("未能完成查詢", context.summary())
        self.assertIn("不代表查無", context.summary())
        self.assertNotIn("SECRET_DSN", context.summary())

    def test_parallel_mixed_results_dedup_and_fresh_turn(self):
        rows = [source("find_rules", "found"), source("get_my_event_progress", "found"),
                source("find_rules", "found"), source("find_rules", "no_match")]
        errors = [source("find_rules", "tool_error"), source("find_rules", "tool_error")]
        first = build_limitations(rows, errors)
        self.assertEqual(first.codes, ("rules_found", "rules_no_match", "progress_scope", "tool_error"))
        self.assertEqual(first, build_limitations(list(reversed(rows)), errors))
        self.assertEqual(first.summary().count("已取得活動公告資料"), 1)
        self.assertEqual(build_limitations([], []).summary(), "")
