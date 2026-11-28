"""Exercise the chat entry point with a scripted tool selector and the seeded test DB."""
import asyncio
import json
from unittest.mock import patch

from agent import answer
from answering import DIRECT_REPLIES
from business_replies import FACT_TOOL
from evaluations.eval_rag import missing_outcomes, structural_gate
from evidence import source_alias
from model_client import TransientModelError
from tests.test_bounded_agent import abstain, scripted, select
from tests.test_guide import OfflineTests
from tests.test_single_agent import call, response


def fact(question, kind, product="", edition="current", account="public"):
    return call(FACT_TOOL, question=question, kind=kind, product=product, edition=edition, account=account)


class BusinessReplyTests(OfflineTests):
    def ask(self, question, *replies, history=(), player="A"):
        events = []
        model = scripted(*replies)
        reply, trace = asyncio.run(answer(player, "ollama", history, question, model,
                                          lambda t, d: events.append((t, d))))
        self.assertEqual(model.call_count, len(replies))
        return reply, trace, dict(events)["Python 檢查回答依據"]["子問題"]

    def test_gap_calculates_both_sides_and_never_promises_eligibility(self):
        for player, name, expected in [("A", "霧港領航員", "還差 20"), ("A", "霧潮貼圖", "已達價格門檻"),
                                       ("C", "海圖航線框", "還差 35")]:
            with self.subTest(player=player, name=name):
                q = f"我想換{name}，星星幣夠了嗎？"
                text, trace, checks = self.ask(q, select(fact(q, "redemption_gap", name, account="self")), player=player)
                self.assertIn(expected, text)
                self.assertIn("不代表已符合所有兌換資格", text)
                self.assertEqual([t["工具"] for t in trace], ["find_shop_item", "get_my_event_progress"])
                self.assertEqual(len(checks[0]["採用段落"]), 2)

    def test_independent_missing_item_and_read_only_action(self):
        a, b, c = "我的餘額是多少？", "七龍珠模型要多少？", "順便幫我加二十枚。"
        text, trace, checks = self.ask(a+b+c, select(fact(a, "balance", account="self"),
                                          fact(b, "item_price", "七龍珠模型"), fact(c, "unsupported")))
        self.assertIn("80 枚", text)
        self.assertIn("查無「七龍珠模型」", text)
        self.assertIn(DIRECT_REPLIES["unsupported"], text)
        self.assertEqual([p["判定"] for p in checks], ["supported", "supported", "unsupported"])
        self.assertEqual(len(trace), 2)

    def test_missing_or_ambiguous_dependent_item_never_reads_private_data(self):
        for name, status in [("星際飛船模型", "supported"), ("霧", "clarify"), ("", "clarify")]:
            q = f"我離換到{name}還差多少？"
            with self.subTest(name=name), patch("guide.read_progress", side_effect=AssertionError) as db:
                text, trace, checks = self.ask(q, select(fact(q, "redemption_gap", name, account="self")))
                self.assertEqual(checks[0]["判定"], status)
                self.assertNotIn("get_my_event_progress", [t["工具"] for t in trace])
                if status == "clarify":
                    self.assertIn("哪一件商城商品", text)
                db.assert_not_called()

    def test_other_or_unspecified_account_and_failed_database(self):
        for scope, status in [("other", "account_limit"), ("unclear", "clarify")]:
            q = "可以查餘額嗎？"
            text, trace, checks = self.ask(q, select(fact(q, "balance", account=scope)))
            self.assertFalse(trace)
            self.assertEqual(checks[0]["判定"], status)
        a, b = "我的餘額多少？", "霧潮貼圖多少？"
        with patch("guide.read_progress", side_effect=OSError("database offline")):
            text, _, checks = self.ask(a+b, select(fact(a, "balance", account="self"), fact(b, "item_price", "霧潮貼圖")))
        self.assertEqual([p["判定"] for p in checks], ["no_evidence", "supported"])
        self.assertIn("20 枚", text)
        self.assertNotIn("0 枚星星幣（紀錄", text)

    def test_followup_uses_only_a_single_focused_product_and_a_fresh_balance(self):
        q, later = "霧潮貼圖賣多少？", "那我還差多少？"
        _, trace, _ = self.ask(q, select(fact(q, "item_price", "霧潮貼圖")))
        history = [{"role": "user", "content": q}, {"role": "assistant", "content": "餘額是 999", "trace": trace}]
        text, reads, _ = self.ask(later, select(fact(later, "redemption_gap", edition="context", account="self")), history=history)
        self.assertIn("80 枚", text)
        self.assertEqual(len(reads), 2)
        q = "我想看商城所有商品"
        _, trace, _ = self.ask(q, select(fact(q, "catalog")))
        history[-1]["trace"] = trace
        text, reads, _ = self.ask(later, select(fact(later, "redemption_gap", edition="context", account="self")), history=history)
        self.assertIn("哪一件商城商品", text)
        self.assertFalse(reads)

    def test_facts_survive_mixed_rule_rounds_and_a_followup_transport_error(self):
        a, b = "霧潮貼圖多少錢？", "退款手續費是多少？"
        def finish(provider, messages, tools):
            assistant = next(m for m in messages if m["role"] == "assistant")
            ids = [c["id"] for c in assistant["tool_calls"]]
            self.assertEqual(ids, [m["tool_call_id"] for m in messages if m["role"] == "tool"])
            return response(abstain(b))
        def failed(*args):
            raise TransientModelError("unavailable")
        for final in (finish, failed):
            text, _, checks = self.ask(a+b, select(fact(a, "item_price", "霧潮貼圖"),
                       call("find_rules", mode="search", edition="current", query=b)), final)
            self.assertIn("20 枚", text)
            self.assertEqual([(p["問題"].strip("？"), p["判定"]) for p in checks],
                             [(a.strip("？"), "supported"), (b.strip("？"), "no_evidence")])

    def test_verified_order_followup_does_not_reuse_old_answer_or_a_different_edition(self):
        q, later = "霧港商城顯示 SG-204", "那我要去哪裡查這筆兌換？"
        key = "2026-11/shop/訂單處理中"
        def cited(question):
            return response({"question": question, "status": "supported", "answer": "到兌換紀錄查詢。",
                             "source_ids": [source_alias(key)]})
        _, trace, _ = self.ask(q, select(call("find_rules", mode="search", edition="current", query=q)), cited(q))
        history = [{"role": "user", "content": q}, {"role": "assistant", "content": "錯誤的舊回答", "trace": trace}]
        follow = lambda edition: select(call("find_rules", mode="search", edition=edition, query=later, previous_issue=key))
        text, _, checks = self.ask(later, follow("context"), cited(later), history=history)
        self.assertIn("兌換紀錄", text)
        self.assertEqual(checks[0]["採用段落"][0]["id"], key)
        _, reads, _ = self.ask(later, follow("previous"), response(abstain(later)), history=history)
        self.assertFalse(reads)
        other = "我換一筆，它顯示 XY-999。"
        _, reads, _ = self.ask(other, follow("current"), response(abstain(other)), history=history)
        self.assertFalse(reads)
        history[-1]["trace"] = []
        _, reads, _ = self.ask(later, follow("current"), response(abstain(later)), history=history)
        self.assertFalse(reads)

    def test_batch_rejects_balance_only_and_citations_without_delivered_facts(self):
        from pathlib import Path
        cases = json.loads((Path(__file__).resolve().parents[1] / "evaluations/task_cases.json").read_text())
        balance = {"判定": "supported", "問題": "我的餘額與差額 20？", "呈現主張": "餘額 80 枚",
                   "採用段落": [{"id": "2026-11/my-progress", "quote": "80"}]}
        turn = {"reply": "餘額 80 枚", "checks": [{"子問題": [balance]}],
                "tools": [{"name": "get_my_event_progress", "arguments": {"edition": "2026-11"}}]}
        for c in (c for c in cases if c["id"] in {"unknown-item-price", "redemption-gap"}):
            with self.subTest(case=c["id"]):
                self.assertTrue(any("missing outcome:" in msg for msg in
                    structural_gate(c, [turn], {"missing_retrieval": [], "missing_answer": []})))
        gap = next(c for c in cases if c["id"] == "redemption-gap")
        balance["採用段落"].append({"id": "2026-11/shop-items/fog-harbor-navigator", "quote": "霧港領航員價格100，差20"})
        self.assertTrue(missing_outcomes(gap, [balance]))
