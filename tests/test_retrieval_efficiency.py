"""Exact vector reuse and scope isolation; all external I/O is mocked."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from backend import QueryMemo, ToolRegistry
from embedding_client import EmbeddingSettings
from guide import CATALOG
from request_context import RequestContext
from vector_search import hybrid_search


class FakeIndex:
    """Only public candidate ids, not real database access."""
    def __init__(self):
        self.scopes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, args=()):
        if "to_regclass" in sql and "pg_attribute" not in sql:
            return SimpleNamespace(fetchone=lambda: ("event_support_agent_embeddings",))
        if "pg_attribute" in sql:
            return SimpleNamespace(fetchone=lambda: (True,))
        if "SELECT count(*)" in sql:
            self.scopes.append(tuple(args[0:4]))
            return SimpleNamespace(fetchone=lambda: (len(args[-1]), 2, 2))
        if "ORDER BY similarity" in sql:
            ids = args[-2]
            assert json.loads(args[1]) == [1.0, 0.0]
            return SimpleNamespace(fetchall=lambda: [(i, 0.8, len(ids)) for i in sorted(ids)[:args[-1]]])
        raise AssertionError(sql)


def passage(edition):
    return {"段落ID": edition + "/test/rule", "檔期": edition, "文件": "規則",
            "文件ID": "test", "發布日": "2026-10-01", "章節": "規則", "原文": "測試規則"}


class RetrievalEfficiencyTests(unittest.TestCase):
    def test_single_flight_and_failed_computation_is_not_retained(self):
        memo = QueryMemo()
        started, release = Event(), Event()
        def compute():
            started.set()
            if not release.wait(3):
                raise RuntimeError("test timeout")
            return [1.0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(memo.get, "same", compute)
            self.assertTrue(started.wait(3))
            b = pool.submit(memo.get, "same", compute)
            release.set()
            self.assertEqual(a.result(timeout=3), b.result(timeout=3))
        self.assertEqual(memo.stats(), {"requests": 2, "computations": 1, "reuses": 1, "failures": 0})
        def fail():
            raise RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            memo.get("failure", fail)
        self.assertEqual(memo.get("failure", lambda: [2.0]), [2.0])
        self.assertEqual(memo.stats(), {"requests": 4, "computations": 3, "reuses": 1, "failures": 1})

    def test_vector_reuse_keeps_independent_edition_rankings(self):
        memo, index = QueryMemo(), FakeIndex()
        settings = EmbeddingSettings("nvidia", "model", "https://example.invalid")
        first, second = [passage("2026-10")], [passage("2026-11")]
        with patch("vector_search.connect", return_value=index), patch("vector_search.embed", return_value=[[1.0, 0.0]]) as embed:
            uncached = [hybrid_search("相同問題", p, p, [], 3, settings=settings) for p in (first, second)]
            self.assertEqual(embed.call_count, 2)
            embed.reset_mock()
            cached = [hybrid_search("相同問題", p, p, [], 3, settings=settings, embed_once=memo.get)
                      for p in (first, second)]
            self.assertEqual(embed.call_count, 1)
            self.assertEqual(cached, uncached)
            self.assertEqual([rows[0]["檔期"] for rows in cached], ["2026-10", "2026-11"])
            self.assertEqual(len(index.scopes), 4)
            self.assertEqual([x[2] for x in index.scopes], ["2026-10", "2026-11"] * 2)
            hybrid_search("不同問題", first, first, [], 3, settings=settings, embed_once=memo.get)
            other = EmbeddingSettings("nvidia", "another-model", settings.endpoint)
            hybrid_search("相同問題", first, first, [], 3, settings=other, embed_once=memo.get)
            other_endpoint = EmbeddingSettings("nvidia", "model", "https://other.invalid")
            hybrid_search("相同問題", first, first, [], 3, settings=other_endpoint, embed_once=memo.get)
            hybrid_search("相同問題", first, first, [], 3, settings=settings, embed_once=QueryMemo().get)
            self.assertEqual(embed.call_count, 5)
        self.assertEqual(memo.stats(), {"requests": 5, "computations": 4, "reuses": 1, "failures": 0})

    def test_invalid_index_is_checked_even_when_vector_is_cached(self):
        memo = QueryMemo()
        settings = EmbeddingSettings("nvidia", "model", "https://example.invalid")
        rows = [passage("2026-10")]
        with patch("vector_search.connect", return_value=FakeIndex()), patch("vector_search.embed", return_value=[[1.0, 0.0]]):
            hybrid_search("q", rows, rows, [], 3, settings=settings, embed_once=memo.get)
        invalid = FakeIndex()
        invalid.execute = lambda *args: SimpleNamespace(fetchone=lambda: (None,))
        from embedding_client import EmbeddingError
        with patch("vector_search.connect", return_value=invalid), patch("vector_search.embed") as embed:
            with self.assertRaises(EmbeddingError):
                hybrid_search("q", rows, rows, [], 3, settings=settings, embed_once=memo.get)
            embed.assert_not_called()
        self.assertEqual(memo.stats()["requests"], 1)

    def test_registry_uses_turn_local_query_memo(self):
        settings = EmbeddingSettings("nvidia", "model", "https://example.invalid")
        first = ToolRegistry("A", embedding=settings)
        second = ToolRegistry("A", embedding=settings)
        self.assertIsNot(first.query_vectors, second.query_vectors)
        context = RequestContext.build("活動規則", [])
        args = first.validate("find_rules", {"edition": "current", "mode": "search", "query": "活動規則"}, context, {})
        with patch.dict("os.environ", {"EVENT_SUPPORT_AGENT_RETRIEVAL": "hybrid"}):
            # Registry resolves retriever during construction.
            registry = ToolRegistry("A", embedding=settings)
        with patch("vector_search.hybrid_search", return_value=[]) as rank:
            registry.execute("find_rules", args)
        self.assertEqual(rank.call_args.kwargs["embed_once"], registry.query_vectors.get)
