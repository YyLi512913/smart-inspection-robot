"""MySQL 访问助手：SQLAlchemy Core + text()，按语句取连接（MVP 量级足够）。

注意：paho 回调线程与 FastAPI 协程都会调用，engine 连接池本身线程安全。
"""
from contextlib import contextmanager

from sqlalchemy import create_engine, text

from . import config

engine = create_engine(
    config.MYSQL_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    future=True,
)


def query(sql: str, params: dict | None = None) -> list[dict]:
    """SELECT：返回 dict 列表。"""
    with engine.connect() as conn:
        return [dict(m) for m in conn.execute(text(sql), params or {}).mappings()]


def query_one(sql: str, params: dict | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: dict | None = None) -> int:
    """INSERT/UPDATE/DELETE：返回 lastrowid（INSERT 时有意义）。"""
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        return result.lastrowid


@contextmanager
def transaction():
    """多语句原子流转（状态机用）。yield 出 Connection，用 conn.execute(text(...))。"""
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()
