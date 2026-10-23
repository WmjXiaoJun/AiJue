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


# 轻量迁移表：老库缺列时自动 ALTER TABLE ADD COLUMN（JSON 列在 SQLite 用 TEXT 存）
_MIGRATIONS = {
    "jobs": {
        "character_id": "VARCHAR(64)",
    },
    "characters": {
        "concept_portrait_url": "VARCHAR(400)",
        "concept_sheet_url": "VARCHAR(400)",
        "concept_three_view": "TEXT",
        "concept_expressions": "TEXT",
        "h3_video_url": "VARCHAR(400)",
        "h3_portrait_url": "VARCHAR(400)",
    },
}


def migrate_columns() -> None:
    """轻量迁移：老库补新增列（SQLite 用 ALTER TABLE ADD COLUMN，可重复执行）。"""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, columns in _MIGRATIONS.items():
        if table not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        missing = {name: ddl for name, ddl in columns.items() if name not in existing}
        if not missing:
            continue
        with engine.begin() as connection:
            for name, ddl in missing.items():
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    # JSON 列老数据补空对象（避免 None 进 Pydantic Dict 字段）
    if "characters" in tables:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE characters SET concept_three_view = '{}' "
                    "WHERE concept_three_view IS NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE characters SET concept_expressions = '{}' "
                    "WHERE concept_expressions IS NULL"
                )
            )


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
