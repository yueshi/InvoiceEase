from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Numeric, String
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """naive-UTC 当前时间：SQLite 字符串比较一致；PG 对齐时改用 timestamptz 语义。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Money(TypeDecorator):
    """金额：PostgreSQL 用 NUMERIC(14,2)；SQLite 用 TEXT 存字符串，杜绝浮点精度损失。"""

    impl = Numeric(14, 2)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(32))
        return dialect.type_descriptor(Numeric(14, 2))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "sqlite":
            return str(value)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)
