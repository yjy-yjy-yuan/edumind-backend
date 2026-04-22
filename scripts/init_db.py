#!/usr/bin/env python
"""数据库初始化脚本 - 只管理后端当前使用的 MySQL 表。"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.dialects import mysql
from sqlalchemy.engine.url import make_url
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable
from sqlalchemy.schema import DropTable

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings
from app.core.database import engine
from app.models import Base
from app.models import Note
from app.models import Question
from app.models import RecommendationOpsEvent
from app.models import SemanticSearchLog
from app.models import Subtitle
from app.models import User
from app.models import Video
from app.models.note import NoteTimestamp
from app.models.similarity_audit_log import SimilarityAuditLogModel

TABLE_DESCRIPTIONS = {
    "users": "用户信息表",
    "videos": "视频信息表",
    "subtitles": "字幕数据表",
    "notes": "学习笔记表",
    "note_timestamps": "笔记时间戳关联表",
    "questions": "问答记录表",
    "recommendation_ops_events": "推荐运营事件日志表",
    "semantic_search_logs": "语义搜索全局检索日志表",
    "similarity_audit_logs": "标签相似度 LLM 审计日志表",
}

MANAGED_TABLE_NAMES = {
    User.__tablename__,
    Video.__tablename__,
    Subtitle.__tablename__,
    Note.__tablename__,
    NoteTimestamp.__tablename__,
    Question.__tablename__,
    RecommendationOpsEvent.__tablename__,
    SemanticSearchLog.__tablename__,
    SimilarityAuditLogModel.__tablename__,
}


def get_managed_tables():
    """返回当前后端显式管理的表，顺序按外键依赖排序。"""
    return [table for table in Base.metadata.sorted_tables if table.name in MANAGED_TABLE_NAMES]


def masked_database_target() -> str:
    """隐藏密码后的连接目标。"""
    try:
        url = make_url(settings.DATABASE_URL)
        host = url.host or "localhost"
        port = url.port or 3306
        database = url.database or "<unknown>"
        driver = url.drivername
        username = url.username or "<unknown>"
        return f"{driver}://{username}:***@{host}:{port}/{database}"
    except Exception:
        return "<invalid DATABASE_URL>"


def print_managed_tables():
    print("\n当前脚本只管理以下表:")
    for table in get_managed_tables():
        print(f"  - {table.name}")


def sync_users_table_schema():
    """为现有 users 表补齐当前认证链路所需字段和索引。"""
    if engine.dialect.name != "mysql":
        return

    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    columns = {column["name"]: column for column in inspector.get_columns("users")}
    indexes = {index["name"] for index in inspector.get_indexes("users")}
    statements = []

    email_column = columns.get("email")
    if email_column and not email_column.get("nullable", True):
        statements.append("ALTER TABLE users MODIFY COLUMN email VARCHAR(120) NULL")

    password_hash_column = columns.get("password_hash")
    password_hash_length = getattr(password_hash_column.get("type"), "length", None) if password_hash_column else None
    if password_hash_column and password_hash_length != 255:
        statements.append("ALTER TABLE users MODIFY COLUMN password_hash VARCHAR(255) NOT NULL")

    if "phone" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN phone VARCHAR(32) NULL")
    if "password_fingerprint" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN password_fingerprint VARCHAR(64) NULL")
    if "login_count" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN login_count INTEGER NOT NULL DEFAULT 0")

    if "ix_users_phone" not in indexes:
        statements.append("CREATE UNIQUE INDEX ix_users_phone ON users (phone)")
    if "ix_users_password_fingerprint" not in indexes:
        statements.append("CREATE UNIQUE INDEX ix_users_password_fingerprint ON users (password_fingerprint)")

    if not statements:
        print("users 表认证字段已是最新结构。")
        return

    print("正在同步 users 表认证字段...")
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
    print("users 表认证字段同步完成。")


def sync_videos_table_schema():
    """为现有 videos 表补齐离线同步字段。"""
    if engine.dialect.name != "mysql":
        return

    inspector = inspect(engine)
    if "videos" not in inspector.get_table_names():
        return

    columns = {column["name"]: column for column in inspector.get_columns("videos")}
    statements = []

    if "processing_origin" not in columns:
        statements.append(
            "ALTER TABLE videos ADD COLUMN processing_origin "
            "ENUM('ONLINE_BACKEND','IOS_OFFLINE') NOT NULL DEFAULT 'ONLINE_BACKEND'"
        )

    if not statements:
        print("videos 表离线同步字段已是最新结构。")
        return

    print("正在同步 videos 表离线同步字段...")
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
    print("videos 表离线同步字段同步完成。")


def init_database():
    """创建缺失的后端业务表，不删除现有数据。"""
    managed_tables = get_managed_tables()
    print(f"数据库连接: {masked_database_target()}")
    print("正在创建缺失的数据库表...")
    Base.metadata.create_all(bind=engine, tables=managed_tables, checkfirst=True)
    sync_users_table_schema()
    sync_videos_table_schema()
    print("数据库表创建完成。")
    print_managed_tables()


def reset_database():
    """删除并重建当前后端管理的表。"""
    managed_tables = get_managed_tables()
    print(f"数据库连接: {masked_database_target()}")
    print("正在删除并重建当前后端管理的表...")
    Base.metadata.drop_all(bind=engine, tables=list(reversed(managed_tables)), checkfirst=True)
    Base.metadata.create_all(bind=engine, tables=managed_tables, checkfirst=True)
    print("数据库表已重建。")
    print_managed_tables()


def emit_mysql_sql(output_path: str):
    """导出 MySQL 建表 SQL，便于在 Navicat 中手动执行。"""
    managed_tables = get_managed_tables()
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    dialect = mysql.dialect()
    database_name = ""
    try:
        database_name = make_url(settings.DATABASE_URL).database or ""
    except Exception:
        database_name = ""

    lines = [
        "-- EduMind managed MySQL schema",
        "-- Generated by backend_fastapi/scripts/init_db.py",
        "",
    ]
    if database_name:
        lines.extend(
            [
                f"CREATE DATABASE IF NOT EXISTS `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
                f"USE `{database_name}`;",
                "",
            ]
        )
    lines.extend(
        [
            "SET NAMES utf8mb4;",
            "SET FOREIGN_KEY_CHECKS = 0;",
            "",
        ]
    )

    for table in reversed(managed_tables):
        lines.append(f"-- Drop table: {table.name}")
        lines.append(str(DropTable(table, if_exists=True).compile(dialect=dialect)).rstrip() + ";")
        lines.append("")

    for table in managed_tables:
        lines.append(f"-- Create table: {table.name}")
        lines.append(str(CreateTable(table).compile(dialect=dialect)).rstrip() + ";")
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            lines.append(str(CreateIndex(index).compile(dialect=dialect)).rstrip() + ";")
        lines.append("")

    lines.append("SET FOREIGN_KEY_CHECKS = 1;")
    lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"MySQL 建表 SQL 已导出到: {target}")
    print_managed_tables()


def show_table_info():
    """显示当前后端管理的表结构信息。"""
    print("\n" + "=" * 60)
    print("EduMind 后端管理的 MySQL 表结构")
    print("=" * 60)
    print(f"数据库目标: {masked_database_target()}")

    for table in get_managed_tables():
        print(f"\n[{table.name}] - {TABLE_DESCRIPTIONS.get(table.name, '业务表')}")
        print("-" * 50)
        for column in table.columns:
            nullable = "NULL" if column.nullable else "NOT NULL"
            default = ""
            if column.default is not None and getattr(column.default, "arg", None) is not None:
                default = f" default={column.default.arg}"
            primary = " PK" if column.primary_key else ""
            print(f"  {column.name:20} {str(column.type):25} {nullable}{primary}{default}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EduMind MySQL 表管理工具")
    parser.add_argument("--info", action="store_true", help="显示当前后端管理的表结构信息")
    parser.add_argument("--create", action="store_true", help="只创建缺失表，不删除现有数据")
    parser.add_argument("--reset", action="store_true", help="删除并重建当前后端管理的表")
    parser.add_argument("--emit-sql", metavar="PATH", help="导出可在 Navicat 执行的 MySQL SQL 文件")
    args = parser.parse_args()

    if args.info:
        show_table_info()
    elif args.reset:
        reset_database()
    elif args.create:
        init_database()
    elif args.emit_sql:
        emit_mysql_sql(args.emit_sql)
    else:
        show_table_info()
        print_managed_tables()
