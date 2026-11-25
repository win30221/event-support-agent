"""Persistent PostgreSQL demo progress and an explicit, non-destructive seed command."""
import json
from datetime import date
from pathlib import Path

from database import connect


SEED_PATH = Path(__file__).with_name("player_progress_seed.json")


def seed_rows(path=SEED_PATH):
    """Validate the seed before opening a database transaction."""
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("玩家初始資料須為陣列。")
    rows, keys = [], set()
    for row in records:
        if not isinstance(row, dict) or set(row) != {"edition", "player", "recorded_on", "coins", "rounds", "joined"}:
            raise ValueError("玩家初始資料欄位不符。")
        edition, player = row["edition"], row["player"]
        if (not isinstance(edition, str) or not edition.strip()
                or not isinstance(player, str) or not player.strip()
                or type(row["coins"]) is not int or row["coins"] < 0
                or type(row["rounds"]) is not int or row["rounds"] < 0
                or type(row["joined"]) is not bool
                or not isinstance(row["recorded_on"], str)):
            raise ValueError("玩家初始資料型別或數值不符。")
        recorded = date.fromisoformat(row["recorded_on"])
        key = (edition, player)
        if key in keys:
            raise ValueError("玩家初始資料包含重複的檔期及帳號。")
        keys.add(key)
        rows.append((edition, player, recorded, row["coins"], row["rounds"], row["joined"]))
    return rows


# 僅由管理者明確執行初始化；重跑只補缺少的示範紀錄，不覆蓋已持久化的資料。
def initialize_progress(path=SEED_PATH):
    rows = seed_rows(path)
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(721915035)")
        cur.execute("""CREATE TABLE IF NOT EXISTS player_event_progress (
            edition text NOT NULL,
            player text NOT NULL,
            recorded_on date NOT NULL,
            coins bigint NOT NULL CHECK (coins >= 0),
            rounds integer NOT NULL CHECK (rounds >= 0),
            joined boolean NOT NULL,
            PRIMARY KEY (edition, player)
        )""")
        cur.executemany("""INSERT INTO player_event_progress
            (edition, player, recorded_on, coins, rounds, joined)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (edition, player) DO NOTHING""", rows)
        return cur.rowcount


# 查詢使用後端綁定帳號及檔期；不載入 seed、不建表、不修改餘額。
def read_progress(player, edition):
    with connect() as db:
        db.read_only = True
        return db.execute("""SELECT recorded_on, coins, rounds, joined
            FROM player_event_progress WHERE edition = %s AND player = %s""",
            (edition, player)).fetchone()


if __name__ == "__main__":
    print(f"玩家進度資料表已初始化，新增 {initialize_progress()} 筆示範紀錄（保留既有資料）。")
