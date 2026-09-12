from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from invoicing.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)


# C5 修复：SQLite 默认关闭外键约束（PRAGMA foreign_keys=OFF）。
# 注册 connect 钩子，每个连接打开时启用 FK 校验——否则迁移加的 ondelete=SET NULL
# 对 SQLite 无效，悬空引用继续静默接受。
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
