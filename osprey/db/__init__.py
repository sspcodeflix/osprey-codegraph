from pathlib import Path

import psycopg

from osprey.config import settings

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(settings.db_dsn, autocommit=autocommit)


def init_db() -> None:
    with connect() as conn:
        conn.execute(SCHEMA.read_text())
