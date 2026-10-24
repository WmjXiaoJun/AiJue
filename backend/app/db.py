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
        "h3_metaso_url": "VARCHAR(400)",
    },
    "episodes": {
        "script": "TEXT NOT NULL DEFAULT ''",
    },
    "shots": {
        "first_frame_url": "VARCHAR(400)",
        "last_frame_url": "VARCHAR(400)",
        "h3_video_url": "VARCHAR(400)",
        "dynamic_mode": "VARCHAR(32)",
        "dynamic_status": "VARCHAR(16) NOT NULL DEFAULT 'none'",
    },
    "shot_versions": {
        "kind": "VARCHAR(16) NOT NULL DEFAULT 'clip'",
    },
    "settings": {
        "h3_provider": "VARCHAR(16) NOT NULL DEFAULT 'local'",
        "metaso_base_url": "VARCHAR(300) NOT NULL DEFAULT 'https://metaso.cn/api/minimax'",
        "metaso_resolution": "VARCHAR(16) NOT NULL DEFAULT '768P'",
        "metaso_duration": "INTEGER NOT NULL DEFAULT 5",
        "localize_langs": "TEXT",
        "localize_translator": "VARCHAR(16)",
        "localize_voices": "TEXT",
        "upload_post_username": "VARCHAR(120)",
        "upload_post_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "upload_post_platforms": "TEXT",
        "upload_post_youtube_privacy_status": "VARCHAR(16)",
        "upload_post_youtube_made_for_kids": "BOOLEAN NOT NULL DEFAULT 0",
        "publish_brand_title": "VARCHAR(200)",
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
    # 动态镜头列老数据兜底
    if "shots" in tables:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE shots SET dynamic_status = 'none' WHERE dynamic_status IS NULL OR dynamic_status = ''")
            )
    if "shot_versions" in tables:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE shot_versions SET kind = 'clip' WHERE kind IS NULL OR kind = ''")
            )
    # 台本列老数据兜底
    if "episodes" in tables:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE episodes SET script = '' WHERE script IS NULL")
            )
    # 设置表新增列老数据兜底
    if "settings" in tables:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE settings SET h3_provider = 'local' WHERE h3_provider IS NULL OR h3_provider = ''")
            )
            connection.execute(
                text(
                    "UPDATE settings SET metaso_base_url = 'https://metaso.cn/api/minimax' "
                    "WHERE metaso_base_url IS NULL OR metaso_base_url = ''"
                )
            )
            connection.execute(
                text("UPDATE settings SET metaso_resolution = '768P' WHERE metaso_resolution IS NULL OR metaso_resolution = ''")
            )
            connection.execute(
                text("UPDATE settings SET metaso_duration = 5 WHERE metaso_duration IS NULL")
            )
            connection.execute(
                text(
                    "UPDATE settings SET localize_langs = '[\"en\",\"es\"]' "
                    "WHERE localize_langs IS NULL OR localize_langs = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE settings SET localize_translator = 'ollama' "
                    "WHERE localize_translator IS NULL OR localize_translator = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE settings SET localize_voices = '{}' "
                    "WHERE localize_voices IS NULL OR localize_voices = ''"
                )
            )
            connection.execute(
                text("UPDATE settings SET upload_post_username = '' WHERE upload_post_username IS NULL")
            )
            connection.execute(
                text("UPDATE settings SET upload_post_enabled = 0 WHERE upload_post_enabled IS NULL")
            )
            connection.execute(
                text(
                    "UPDATE settings SET upload_post_platforms = '"
                    + '["youtube","tiktok","instagram"]'
                    + "' WHERE upload_post_platforms IS NULL OR upload_post_platforms = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE settings SET upload_post_youtube_privacy_status = 'unlisted' "
                    "WHERE upload_post_youtube_privacy_status IS NULL OR upload_post_youtube_privacy_status = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE settings SET upload_post_youtube_made_for_kids = 0 "
                    "WHERE upload_post_youtube_made_for_kids IS NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE settings SET publish_brand_title = '造片局 · AI微综艺' "
                    "WHERE publish_brand_title IS NULL OR publish_brand_title = ''"
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
