"""Real PostgreSQL integration tests; never modify the demo schema."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql
from psycopg.errors import UndefinedTable

from database import connect
from guide import CATALOG, progress, published_passages
from player_progress import initialize_progress, read_progress, seed_rows


class PlayerProgressTests(unittest.TestCase):
    def setUp(self):
        self.schema = "progress_test_" + uuid4().hex
        with connect() as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        patcher = patch("player_progress.connect", side_effect=self.scoped_connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def scoped_connect(self):
        db = connect()
        # Search path is session-local; tests use a separate schema in every connection.
        db.autocommit = True
        db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        db.autocommit = False
        return db

    def drop_schema(self):
        with connect() as db:
            db.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema)))

    def test_seed_and_read_across_connections(self):
        self.assertEqual(initialize_progress(), 3)
        self.assertEqual(read_progress("A", CATALOG.current), (date(2026, 11, 21), 80, 2, True))
        self.assertEqual(read_progress("B", CATALOG.current), (date(2026, 11, 21), 80, 3, True))
        self.assertEqual(read_progress("C", CATALOG.current), (date(2026, 11, 21), 0, 0, False))
        self.assertIsNone(read_progress("A", CATALOG.previous))

    def test_reinitialization_does_not_overwrite_persistent_data(self):
        initialize_progress()
        with self.scoped_connect() as db:
            db.execute("UPDATE player_event_progress SET coins = %s, rounds = %s WHERE edition = %s AND player = %s",
                       (100, 3, CATALOG.current, "A"))
        self.assertEqual(initialize_progress(), 0)
        self.assertEqual(progress("A")["星星幣"], 100)
        self.assertEqual(progress("B")["星星幣"], 80)

    def test_account_and_edition_query_uses_bound_parameters(self):
        initialize_progress()
        self.assertIsNone(read_progress("A' OR '1'='1", CATALOG.current))
        self.assertIsNone(read_progress("A", "2026-11' OR '1'='1"))
        self.assertEqual(progress("C")["星星幣"], 0)
        self.assertIn("狀態", progress("A", "previous"))

    def test_missing_schema_is_not_a_missing_player_record(self):
        with self.assertRaises(UndefinedTable):
            progress("A")

    def test_stale_record_date_is_preserved(self):
        initialize_progress()
        with self.scoped_connect() as db:
            db.execute("UPDATE player_event_progress SET recorded_on = %s WHERE edition = %s AND player = %s",
                       (date(2026, 11, 20), CATALOG.current, "A"))
        actual = progress("A")
        self.assertEqual(actual["紀錄日期"], "2026-11-20")
        self.assertEqual(actual["紀錄當日已計入場次"], 2)
        self.assertNotIn("今日已計入場次", actual)

    def test_public_catalog_does_not_contain_seed(self):
        self.assertTrue(seed_rows())
        self.assertTrue(all("players" not in e and "progress_date" not in e
                            for e in CATALOG.editions.values()))
        self.assertNotIn("星星幣餘額", [p["章節"] for p in published_passages(CATALOG.current)])

    def test_duplicate_seed_is_rejected_before_db_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seed.json"
            row = {"edition": CATALOG.current, "player": "X", "recorded_on": "2026-11-21",
                   "coins": 0, "rounds": 0, "joined": False}
            path.write_text(json.dumps([row, row]), encoding="utf-8")
            with self.assertRaises(ValueError):
                initialize_progress(path)
