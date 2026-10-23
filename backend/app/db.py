"""SQLite 引擎、会话与建表初始化。"""

from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

config.ensure_dirs()

DATABASE_URL = f"sqlite:///{config.DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

Base = declarative_base()


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def get_db():
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_columns() -> None:
    """轻量迁移：老库补新增列（SQLite 用 ALTER TABLE ADD COLUMN，可重复执行）。"""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "jobs" in tables:
        columns = {column["name"] for column in inspector.get_columns("jobs")}
        if "character_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE jobs ADD COLUMN character_id VARCHAR(64)"))


def init_db() -> None:
    """建表 + 轻量迁移 + 默认设置 + 空库播种 + 角色播种（可重复执行）。"""
    from . import models, seed  # noqa: F401  确保模型已注册

    config.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    migrate_columns()
    with SessionLocal() as db:
        seed.ensure_defaults(db)
        seed.seed_if_empty(db)
        seed.seed_characters(db)
