"""Regression for the historical intent-first graph; its contracts remain unchanged."""
import asyncio
import json
import unittest
from datetime import date
from unittest.mock import patch

from agent import intent_answer as answer
from answering import DIRECT_REPLIES, ANSWER_VERIFICATION_FAILED
from evidence import source_alias
from guide import CATALOG, published_passages, shop_catalog
from task_business import materialize_plan, inspect_slots, scoped_evidence
from task_contracts import TaskProposal, record
from intent_analysis import CAPABILITIES, SimpleIntentPlan, anchor_intents
from request_context import RequestContext
from tests.test_bounded_agent import call, select, rules, scripted, packet
from tests.test_guide import OfflineTests


CURRENT = CATALOG.current


def output(**data):
    return {"content": json.dumps(data, ensure_ascii=False)}


def task(kind, goal, *, account="none", product="", editions=("current",), depends_on=()):
    return {"kind": kind, "goal": goal, "account": account, "product": product,
            "editions": list(editions), "depends_on": list(depends_on)}


def plan(*tasks):
    """Script the two separate model stages without bypassing the new contracts."""
    intents, bindings = [], []
    for i, t in enumerate(tasks, 1):
        kind = t["kind"]
        category = next(k for k, kinds in CAPABILITIES.items() if kind in kinds)
        intents.append({"type": category, "request": t["goal"]})
        bindings.append({"task_id": f"T{i}", "kind": kind, "editions": t["editions"],
                         "product": t["product"], "account": t["account"]})
    return (output(intents=intents), output(decision="accept", issues=[], bindings=bindings))


def assess(*decisions):
    return output(decisions=list(decisions))


def decision(tid, status="ready", ids=(), needs=(), reason="已核對"):
    return {"task_id": tid, "status": status, "source_ids": [source_alias(i) for i in ids],
            "needs": list(needs), "reason": reason}


def verify(*ids, verdicts=None):
    return output(verdicts=verdicts if verdicts is not None else [
                      {"task_id": tid, "verdict": "pass", "issues": []} for tid in ids])


def draft(tid, text, keys):
    return {"task_id": tid, "answer": text, "source_ids": [source_alias(k) for k in keys]}


class IntentWorkflowTests(OfflineTests):
    def ask(self, question, *responses, history=()):
        events = []
        responses = [step for response in responses for step in (response if isinstance(response, tuple) else (response,))]
        model = scripted(*responses)
        reply, trace = asyncio.run(answer("A", "ollama", list(history), question, model,
                                        lambda t, d: events.append((t, d))))
        self.assertEqual(model.call_count, len(responses))
        check = dict(events)["Python 檢查回答依據"]
        return reply, trace, check, events

    def test_unsupported_operations_do_not_retrieve_or_modify(self):
        for goal in ("贈送星星幣", "代為兌換獎品", "修改活動紀錄"):
            with self.subTest(goal=goal):
                reply, trace, checks, _ = self.ask(goal, plan(task("operation", goal, account="self")),
                                                   verify("T1"))
                self.assertEqual(trace, [])
                self.assertEqual(checks["子問題"][0]["判定"], "unsupported")
                self.assertEqual(checks["需求覆蓋"], "complete")
                self.assertIn("不能代為", reply)

    def test_no_privileged_query_for_another_account(self):
        q = "查別人的星星幣"
        reply, trace, checks, _ = self.ask(q, plan(task("balance", q, account="other")), verify())
        self.assertEqual(trace, [])
        self.assertEqual(checks["子問題"][0]["判定"], "account_limit")
        self.assertEqual(reply, DIRECT_REPLIES["account_limit"])

    def test_catalog_is_assessed_and_rendered_from_all_rows(self):
        q = "商城所有物品價格"
        reply, trace, checks, _ = self.ask(q, plan(task("catalog", q)),
            select(call("list_shop_items", edition="current")),
            assess(decision("T1")), verify("T1"))
        self.assertEqual([x["工具"] for x in trace], ["list_shop_items"])
        self.assertEqual(checks["子問題"][0]["判定"], "supported")
        for item in shop_catalog()["items"]:
            self.assertIn(f"{item['name']}：{item['price']} 枚星星幣", reply)
        self.assertNotIn("其餘需求", reply)

    def test_direct_renderer_does_not_bypass_evidence_assessment(self):
        q = "商城所有物品價格"
        reply, trace, checks, _ = self.ask(q, plan(task("catalog", q)),
            select(call("list_shop_items", edition="current")),
            assess(decision("T1", "no_evidence", reason="候選不足")), verify())
        self.assertEqual(len(trace), 1)
        self.assertEqual(checks["子問題"][0]["判定"], "no_evidence")
        self.assertEqual(reply, DIRECT_REPLIES["no_evidence"])

    def test_balance_and_operation_independent_results(self):
        q = "我有多少星星幣？幫我加二十枚"
        with patch("guide.read_progress", return_value=(date.fromisoformat("2026-11-21"), 73, 2, True)):
            reply, trace, checks, _ = self.ask(q,
                plan(task("balance", "我有多少星星幣？", account="self"), task("operation", "幫我加二十枚", account="self")),
                select(call("get_my_event_progress", edition="current")),
                assess(decision("T1")), verify("T1", "T2"))
        self.assertEqual([x["工具"] for x in trace], ["get_my_event_progress"])
        self.assertEqual([p["判定"] for p in checks["子問題"]], ["supported", "unsupported"])
        self.assertIn("73 枚", reply)
        self.assertIn("不能代為", reply)

    def test_price_gap_requires_product_then_personal_data(self):
        q = "我還差多少才能換霧港領航員？"
        with patch("guide.read_progress", return_value=(date.fromisoformat("2026-11-21"), 73, 2, True)):
            reply, trace, checks, _ = self.ask(q, plan(task("redemption_gap", q, product="霧港領航員", account="self")),
                select(call("find_shop_item", edition="current", name="霧港領航員"),
                       call("get_my_event_progress", edition="current")),
                assess(decision("T1")),
                select(call("get_my_event_progress", edition="current")),
                assess(decision("T1")), verify("T1"))
        self.assertEqual([x["工具"] for x in trace], ["find_shop_item", "get_my_event_progress"])
        price = next(i["price"] for i in shop_catalog()["items"] if i["name"] == "霧港領航員")
        self.assertIn(f"還差 {price - 73} 枚", reply)
        self.assertIn("不代表已符合所有兌換資格", reply)
        self.assertEqual(checks["子問題"][0]["判定"], "supported")

    def test_missing_product_never_queries_balance_for_that_product(self):
        name = "不存在的測試商品"
        q = f"我能兌換{name}嗎？"
        reply, trace, checks, _ = self.ask(q, plan(task("eligibility", q, account="self", product=name)),
            select(call("find_shop_item", edition="current", name=name),
                   call("get_my_event_progress", edition="current")),
            assess(decision("T1")), verify("T1"))
        self.assertEqual([x["工具"] for x in trace], ["find_shop_item"])
        self.assertIn(f"查無「{name}」", reply)
        self.assertEqual(checks["子問題"][0]["判定"], "supported")

    def test_overview_clauses_are_data_driven(self):
        q = "活動總覽"
        keys = [p["段落ID"] for p in published_passages(CURRENT) if p["文件ID"] == "launch"]
        reply, trace, checks, _ = self.ask(q, plan(task("overview", q)),
            select(rules(mode="overview")), assess(decision("T1", ids=keys)),
            output(drafts=[draft("T1", "從大廳參加並完成冒險、到商店兌換。", keys)]), verify("T1"))
        for clause in CATALOG.editions[CURRENT]["overview_required_clauses"]:
            self.assertIn(clause["text"], reply)
        self.assertEqual(checks["子問題"][0]["判定"], "supported")

    def test_verifier_coverage_repairs_drift_from_operation_to_query(self):
        q = "幫我增加星星幣"
        reply, trace, checks, _ = self.ask(q, output(intents=[{"type": "other", "request": q}]),
            output(decision="revise", issues=["原問要求業務操作"], bindings=[]),
            plan(task("operation", q, account="self")), verify())
        self.assertEqual(trace, [])
        self.assertEqual(checks["子問題"][0]["判定"], "unsupported")

    def test_verifier_can_reject_semantically_wrong_knowledge(self):
        q = "冒險規則"
        keys = [f"{CURRENT}/launch/玩法"]
        reply, _, checks, _ = self.ask(q, plan(task("knowledge", q)), select(rules(mode="overview")),
            assess(decision("T1", ids=keys)),
            output(drafts=[draft("T1", "每場冒險都得一千枚。", keys)]),
            verify(verdicts=[{"task_id": "T1", "verdict": "revise", "issues": ["數值錯誤"]}]),
            assess(decision("T1", ids=keys)),
            output(drafts=[draft("T1", "每場冒險都得一千枚。", keys)]),
            verify(verdicts=[{"task_id": "T1", "verdict": "revise", "issues": ["數值錯誤"]}]))
        self.assertEqual(checks["子問題"][0]["判定"], "rejected")
        self.assertNotIn("一千枚", reply)
        self.assertIn(ANSWER_VERIFICATION_FAILED, reply)

    def test_no_new_evidence_preserves_independent_draft_and_stops(self):
        q = "商城價格與未知規則"
        n = {"tool": "find_rules", "edition": "current", "detail": "確認未知規則"}
        reply, trace, checks, _ = self.ask(q, plan(task("catalog", "商城價格"), task("knowledge", "未知規則")),
            select(call("list_shop_items", edition="current"), rules("未知規則")),
            assess(decision("T1"), decision("T2", "needs_retrieval", needs=[n])),
            select(rules("未知規則")), assess(decision("T2", "needs_retrieval", needs=[n])),
            verify("T1", "T2"))
        self.assertEqual([p["判定"] for p in checks["子問題"]], ["supported", "no_evidence"])
        self.assertEqual(len(trace), 2)
        for i in shop_catalog()["items"]:
            self.assertIn(i["name"], reply)

    def test_unknown_source_cannot_become_a_draft(self):
        q = "冒險規則"
        reply, _, checks, _ = self.ask(q, plan(task("knowledge", q)), select(rules(mode="overview")),
            assess(decision("T1", ids=["unknown-source"])), verify())
        self.assertEqual(checks["子問題"][0]["判定"], "rejected")
        self.assertIn(ANSWER_VERIFICATION_FAILED, reply)

    def test_fabricated_intent_text_is_rejected(self):
        question = "新手怎麼玩這個活動？"
        invalid = SimpleIntentPlan.model_validate({"intents": [
            {"type": "knowledge", "request": question},
            {"type": "query", "request": "顯示玩家活動餘額"}]})
        with self.assertRaises(ValueError):
            anchor_intents(invalid, question)

    def test_non_applicable_product_is_discarded_before_lookup(self):
        q = "這個活動怎麼玩？"
        keys = [p["段落ID"] for p in published_passages(CURRENT) if p["文件ID"] == "launch"]
        reply, trace, checks, events = self.ask(q,
            plan(task("overview", q, product="星夜祭")),
            select(rules(mode="overview")), assess(decision("T1", ids=keys)),
            output(drafts=[draft("T1", "從大廳參加並完成冒險、到商店兌換。", keys)]),
            verify("T1"))
        self.assertEqual([x["工具"] for x in trace], ["find_rules"])
        self.assertEqual(checks["子問題"][0]["判定"], "supported")
        self.assertTrue(any(t == "Python 捨棄非適用需求欄位" for t, _ in events))

    def test_invalid_dependency_is_rejected(self):
        context = RequestContext.build("測試", [])
        with self.assertRaises(ValueError):
            materialize_plan([TaskProposal.model_validate(task("knowledge", "測試", depends_on=[1]))], context)


if __name__ == "__main__":
    unittest.main()
