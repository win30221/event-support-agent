"""Shared PostgreSQL connection factory for public retrieval and business reads."""
import os

import psycopg


def connect():
    dsn = os.getenv("EVENT_SUPPORT_AGENT_PG_DSN")
    if not dsn:
        raise ValueError("請設定 EVENT_SUPPORT_AGENT_PG_DSN，指向 PostgreSQL 示範資料庫。")
    return psycopg.connect(dsn, connect_timeout=8)
