import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from guide import CATALOG, EventCatalog, progress, published_passages, search_rules, shop_catalog, shop_item


class OfflineTests(unittest.TestCase):
    def setUp(self):
        config = patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_RETRIEVAL": "bm25"})
        config.start()
        self.addCleanup(config.stop)


class GuideTests(OfflineTests):
    def test_shop_catalogue_uses_the_selected_edition_and_distinguishes_missing_data(self):
        item = shop_item("披 風", "current")
        self.assertEqual((item["status"], item["items"][0]["name"]), ("found", "霧港領航員"))
        self.assertEqual(shop_item("霧港", "current")["status"], "ambiguous")
        self.assertEqual(shop_item("披風", "previous")["status"], "not_found")
        self.assertEqual(len(shop_catalog("previous")["items"]), 5)
        self.assertIn("守燈人", {item["name"] for item in shop_catalog("previous")["items"]})

        original = {"id": "demo", "start": "2026-11-01", "end": "2026-11-30", "title": "另一個檔期"}
        for edition, expected in [(original, "unavailable"), ({**original, "shop_items": []}, "not_found"),
                                  ({**original, "shop_items": [{"name": "未完整商品"}]}, "unavailable")]:
            with self.subTest(edition=edition):
                catalog = EventCatalog({"name": "另一個活動", "as_of": "2026-11-21", "editions": [edition]})
                self.assertEqual(shop_item("測試道具", catalog=catalog)["status"], expected)

    def test_expanded_shop_items_and_new_public_announcements(self):
        for edition, expected_names in [
            ("previous", {"守燈人", "星航相框", "潮聲招呼", "秋港暮色", "守夜燈語"}),
            ("current", {"霧港領航員", "霧潮貼圖", "海圖航線框", "霧港守燈者", "月港夜色"}),
        ]:
            with self.subTest(edition=edition):
                items = shop_catalog(edition)["items"]
                self.assertEqual({item["name"] for item in items}, expected_names)
                self.assertEqual(len({item["id"] for item in items}), len(items))
                self.assertTrue(all(item["description"] for item in items))
        self.assertEqual(shop_item("霧潮貼圖", "current")["items"][0]["price"], 20)
        self.assertEqual(shop_item("星航相框", "previous")["items"][0]["price"], 25)
        self.assertEqual(shop_item("披風", "current")["items"][0]["name"], "霧港領航員")
        self.assertEqual(shop_item("七龍珠模型", "current")["status"], "not_found")
        expected_documents = {"previous": {"story", "souvenirs"},
                              "current": {"story", "souvenirs", "weekend"}}
        for edition, required in expected_documents.items():
            docs = published_passages(edition)
            self.assertTrue(required <= {p["文件ID"] for p in docs})
            self.assertEqual(len({p["段落ID"] for p in docs}), len(docs))
        self.assertTrue(any(p["文件ID"] == "weekend" for p in search_rules("週末巡燈", "current")))

    def test_versions_and_modes(self):
        for ref, phrase in [("本期", "排位不計"), ("上期", "冒險或排位")]:
            self.assertTrue(any(phrase in p["原文"] for p in search_rules("排位玩法", ref)))
        self.assertEqual(search_rules("ZYXQ-9137"), [])
        self.assertEqual(search_rules("ＳＧ－２０４")[0]["章節"], "訂單處理中")
        overview = search_rules("unrelated wording", "current", "overview")
        self.assertEqual({p["章節"] for p in overview}, {"參加", "玩法", "兌換"})
        self.assertTrue(all(p["文件ID"] == "launch" for p in overview))

    def test_effective_notice_and_future_publication(self):
        data = json.loads((Path(__file__).resolve().parents[1] / "events.json").read_text(encoding="utf-8"))
        for as_of, published in [("2026-11-13", "2026-11-01"), ("2026-11-14", "2026-11-14")]:
            with self.subTest(as_of=as_of):
                docs = published_passages("current", EventCatalog({**data, "as_of": as_of}))
                delays = [p for p in docs if p["文件ID"] == "delay"]
                self.assertEqual([p["發布日"] for p in delays], [published])
                self.assertTrue(all(p["發布日"] <= as_of for p in docs))
                self.assertTrue(all(p["檔期"] == "2026-11" for p in docs))
        self.assertNotIn("incident", {p["文件ID"] for p in docs})

    def test_router_catalog_contains_only_effective_public_headings(self):
        release = {"id": "trial", "title": "港務試行", "published": "2026-11-01",
                   "sections": {"潮汐測站": "只有查公告才會收到這份原文。"}}
        draft = {**release, "published": "2026-11-28", "sections": {"未發布的新站": "稍後開放"}}
        catalog = EventCatalog({"name": "航海季", "as_of": "2026-11-21", "editions": [{
            "id": "pilot", "title": "秋航", "start": "2026-11-01", "end": "2026-11-30",
            "sections": {"起航": "歡迎"}, "notices": [draft, release],
            "players": {"private-account": {"coins": 9837}},
        }]})
        index = catalog.index()
        self.assertEqual(index[0]["公告章節"]["港務試行"], ["潮汐測站"])
        self.assertNotIn("private-account", str(index))
        self.assertNotIn("只有查公告", str(index))
        self.assertNotIn("未發布的新站", str(index))

    def test_evaluation_sources_resolve_and_stay_public(self):
        cases = json.loads((Path(__file__).resolve().parents[1] / "evaluations" / "rag_cases.json").read_text(encoding="utf-8"))
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        for case in cases:
            available = {p["段落ID"] for key in case["editions"] for p in published_passages(key)}
            self.assertLessEqual(set(case["sources"]), available, case["id"])
        docs = search_rules("今天第三場還能換造型嗎", "current", "eligibility")
        self.assertTrue({"玩法", "兌換", "參加"} <= {p["章節"] for p in docs})
        self.assertEqual(len({p["段落ID"] for p in docs}), len(docs))
        self.assertNotIn("今日已計入場次", str(docs))

    def test_catalog_rollover_and_gaps(self):
        data = {"name": "另一個活動", "editions": [
            {"id": "winter-b", "title": "第二場", "start": "2027-02-10", "end": "2027-02-12",
             "sections": {"公告": "第二場規則"}},
            {"id": "winter-a", "title": "第一場", "start": "2027-01-30", "end": "2027-02-02",
             "sections": {"公告": "第一場規則"}},
        ]}
        for as_of, current, previous in [("2027-01-29", None, None),
                ("2027-02-02", "winter-a", None), ("2027-02-05", None, "winter-a"),
                ("2027-02-10", "winter-b", "winter-a"), ("2027-02-13", None, "winter-b")]:
            with self.subTest(as_of=as_of):
                catalog = EventCatalog({**data, "as_of": as_of})
                self.assertEqual(catalog.resolve("current"), current)
                self.assertEqual(catalog.resolve("上期"), previous)
                if as_of < "2027-02-10":
                    self.assertIsNone(catalog.resolve("winter-b"))
        catalog = EventCatalog({**data, "as_of": "2027-02-10"})
        self.assertEqual(search_rules("", "previous", "overview", catalog)[0]["原文"], "第一場規則")

    def test_hybrid_only_receives_candidates_from_public_selected_edition(self):
        with patch("vector_search.hybrid_search", return_value=[]) as rank:
            rows = search_rules("兌換", "previous", "eligibility", retriever="hybrid")
        _, candidates, corpus, _ = rank.call_args.args
        self.assertEqual({p["檔期"] for p in corpus}, {CATALOG.previous})
        self.assertTrue(all(p["文件ID"] == "launch" for p in rows))
        self.assertTrue(all(p["文件ID"] != "launch" for p in candidates))

    def test_vector_revision_changes_if_a_policy_is_corrected(self):
        from vector_search import edition_hash
        corpus = published_passages("current")
        self.assertEqual(edition_hash(corpus), edition_hash(list(reversed(corpus))))
        rewritten = [{**corpus[0], "原文": "修訂的規章"}, *corpus[1:]]
        self.assertNotEqual(edition_hash(corpus), edition_hash(rewritten))

    def test_search_and_legacy_eligibility_return_the_same_passages(self):
        cases = json.loads((Path(__file__).resolve().parents[1] / "evaluations" / "rag_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            for edition in case["editions"]:
                with self.subTest(case=case["id"], edition=edition):
                    query = case.get("retrieval_query", case["question"])
                    old = search_rules(query, edition, "eligibility", include_overview=True)
                    new = search_rules(query, edition, "search", include_overview=True)
                    self.assertEqual([p["段落ID"] for p in old], [p["段落ID"] for p in new])

    def test_request_memo_shares_repeated_query_without_losing_distinct_queries(self):
        from concurrent.futures import ThreadPoolExecutor
        from backend import ToolRegistry
        from guide import rank_passages
        from request_context import RequestContext
        registry = ToolRegistry("A")
        ctx = RequestContext.build("活動怎麼參加", [])
        args = [registry.validate("find_rules", {"edition": "current", "query": q, "mode": "search"}, ctx, {})
                for q in ("報名", "冒險")]
        with patch("guide.rank_passages", wraps=rank_passages) as rank:
            with ThreadPoolExecutor(max_workers=2) as pool:
                rows = list(pool.map(lambda a: registry.execute("find_rules", a), args))
        self.assertEqual(rank.call_count, 3)
        self.assertTrue(all(rows))
        modes = next(d for d in registry.definitions() if d["function"]["name"] == "find_rules")
        self.assertEqual(set(modes["function"]["parameters"]["properties"]["mode"]["enum"]),
                         {"overview", "search", "compare"})

    def test_player_and_edition_isolation(self):
        self.assertEqual([progress(p)["今日已計入場次"] for p in "ABC"], [2, 3, 0])
        self.assertFalse(progress("C")["已參加本期"])
        self.assertNotIn("星星幣", progress("A", "previous"))
