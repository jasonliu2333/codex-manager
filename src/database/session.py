"""
数据库会话管理
"""

from contextlib import contextmanager
from typing import Generator
import json
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
import os
import logging

from .models import Base

logger = logging.getLogger(__name__)


def _build_sqlalchemy_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://"):]
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://"):]
    return database_url


class DatabaseSessionManager:
    """数据库会话管理器"""

    def __init__(self, database_url: str = None):
        if database_url is None:
            env_url = os.environ.get("APP_DATABASE_URL") or os.environ.get("DATABASE_URL")
            if env_url:
                database_url = env_url
            else:
                # 优先使用 APP_DATA_DIR 环境变量（PyInstaller 打包后由 webui.py 设置）
                data_dir = os.environ.get('APP_DATA_DIR') or os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    'data'
                )
                db_path = os.path.join(data_dir, 'database.db')
                # 确保目录存在
                os.makedirs(data_dir, exist_ok=True)
                database_url = f"sqlite:///{db_path}"

        self.database_url = _build_sqlalchemy_url(database_url)
        self.engine = create_engine(
            self.database_url,
            connect_args={"check_same_thread": False} if self.database_url.startswith("sqlite") else {},
            echo=False,  # 设置为 True 可以查看所有 SQL 语句
            pool_pre_ping=True  # 连接池预检查
        )

        if self.database_url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _connection_record):
                cursor = dbapi_conn.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA cache_size=-64000")
                finally:
                    cursor.close()
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_db(self) -> Generator[Session, None, None]:
        """
        获取数据库会话的上下文管理器
        使用示例:
            with get_db() as db:
                # 使用 db 进行数据库操作
                pass
        """
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """
        事务作用域上下文管理器
        使用示例:
            with session_scope() as session:
                # 数据库操作
                pass
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def create_tables(self):
        """创建所有表"""
        Base.metadata.create_all(bind=self.engine)

    def drop_tables(self):
        """删除所有表（谨慎使用）"""
        Base.metadata.drop_all(bind=self.engine)

    def migrate_tables(self):
        """
        数据库迁移 - 添加缺失的列
        用于在不删除数据的情况下更新表结构
        """
        if not self.database_url.startswith("sqlite"):
            logger.info("非 SQLite 数据库，跳过自动迁移")
            return

        # 需要检查和添加的新列
        migrations = [
            # (表名, 列名, 列类型)
            ("accounts", "cpa_uploaded", "BOOLEAN DEFAULT 0"),
            ("accounts", "cpa_uploaded_at", "DATETIME"),
            ("accounts", "source", "VARCHAR(20) DEFAULT 'register'"),
            ("accounts", "subscription_type", "VARCHAR(20)"),
            ("accounts", "subscription_at", "DATETIME"),
            ("accounts", "cookies", "TEXT"),
            ("accounts", "oauth_recovery_required", "BOOLEAN"),
            ("accounts", "openai_auth_state", "VARCHAR(32)"),
            ("accounts", "token_validation_state", "VARCHAR(64)"),
            ("accounts", "openai_account_state", "VARCHAR(64)"),
            ("phone_verification_attempts", "provider_quote", "FLOAT"),
            ("phone_verification_attempts", "provider_count", "INTEGER"),
            ("phone_verification_attempts", "result_status", "VARCHAR(32) DEFAULT 'pending'"),
            ("phone_verification_attempts", "failure_type", "VARCHAR(32)"),
            ("proxies", "is_default", "BOOLEAN DEFAULT 0"),
            ("cpa_services", "include_proxy_url", "BOOLEAN DEFAULT 0"),
        ]
        index_migrations = [
            ("ix_account_status", "CREATE INDEX IF NOT EXISTS ix_account_status ON accounts(status)"),
            ("ix_account_email_service", "CREATE INDEX IF NOT EXISTS ix_account_email_service ON accounts(email_service)"),
            ("ix_account_created_at", "CREATE INDEX IF NOT EXISTS ix_account_created_at ON accounts(created_at)"),
            ("ix_account_status_created", "CREATE INDEX IF NOT EXISTS ix_account_status_created ON accounts(status, created_at)"),
            ("ix_accounts_oauth_recovery_required", "CREATE INDEX IF NOT EXISTS ix_accounts_oauth_recovery_required ON accounts(oauth_recovery_required)"),
            ("ix_accounts_openai_auth_state", "CREATE INDEX IF NOT EXISTS ix_accounts_openai_auth_state ON accounts(openai_auth_state)"),
            ("ix_accounts_token_validation_state", "CREATE INDEX IF NOT EXISTS ix_accounts_token_validation_state ON accounts(token_validation_state)"),
            ("ix_accounts_openai_account_state", "CREATE INDEX IF NOT EXISTS ix_accounts_openai_account_state ON accounts(openai_account_state)"),
            ("ix_phone_reputation_provider_phone", "CREATE UNIQUE INDEX IF NOT EXISTS ix_phone_reputation_provider_phone ON phone_number_reputations(sms_provider, phone_number)"),
            ("ix_phone_reputation_blacklisted", "CREATE INDEX IF NOT EXISTS ix_phone_reputation_blacklisted ON phone_number_reputations(blacklisted)"),
            ("ix_phone_reputation_last_seen", "CREATE INDEX IF NOT EXISTS ix_phone_reputation_last_seen ON phone_number_reputations(last_seen_at)"),
            ("ix_phone_verify_service", "CREATE INDEX IF NOT EXISTS ix_phone_verify_service ON phone_verification_attempts(service)"),
            ("ix_phone_verify_phone", "CREATE INDEX IF NOT EXISTS ix_phone_verify_phone ON phone_verification_attempts(phone_number)"),
            ("ix_phone_verify_error_code", "CREATE INDEX IF NOT EXISTS ix_phone_verify_error_code ON phone_verification_attempts(error_code)"),
            ("ix_phone_verify_result_status", "CREATE INDEX IF NOT EXISTS ix_phone_verify_result_status ON phone_verification_attempts(result_status)"),
            ("ix_phone_verify_failure_type", "CREATE INDEX IF NOT EXISTS ix_phone_verify_failure_type ON phone_verification_attempts(failure_type)"),
            ("ix_phone_verify_provider_slot_created", "CREATE INDEX IF NOT EXISTS ix_phone_verify_provider_slot_created ON phone_verification_attempts(sms_provider, provider_slot, created_at)"),
            ("ix_phone_verify_provider_success_created", "CREATE INDEX IF NOT EXISTS ix_phone_verify_provider_success_created ON phone_verification_attempts(sms_provider, success, created_at)"),
        ]

        # 确保新表存在（create_tables 已处理，此处兜底）
        Base.metadata.create_all(bind=self.engine)

        with self.engine.connect() as conn:
            # 数据迁移：将旧的 custom_domain 记录统一为 moe_mail
            try:
                conn.execute(text("UPDATE email_services SET service_type='moe_mail' WHERE service_type='custom_domain'"))
                conn.execute(text("UPDATE accounts SET email_service='moe_mail' WHERE email_service='custom_domain'"))
                conn.commit()
            except Exception as e:
                logger.warning(f"迁移 custom_domain -> moe_mail 时出错: {e}")

            for table_name, column_name, column_type in migrations:
                try:
                    # 检查列是否存在
                    result = conn.execute(text(
                        f"SELECT * FROM pragma_table_info('{table_name}') WHERE name='{column_name}'"
                    ))
                    if result.fetchone() is None:
                        # 列不存在，添加它
                        logger.info(f"添加列 {table_name}.{column_name}")
                        conn.execute(text(
                            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                        ))
                        conn.commit()
                        logger.info(f"成功添加列 {table_name}.{column_name}")
                except Exception as e:
                    logger.warning(f"迁移列 {table_name}.{column_name} 时出错: {e}")

            for index_name, sql in index_migrations:
                try:
                    conn.execute(text(sql))
                    conn.commit()
                    logger.info(f"已确保索引存在: {index_name}")
                except Exception as e:
                    logger.warning(f"创建索引 {index_name} 时出错: {e}")

            # 将高频 extra_data 状态回填到独立列
            try:
                result = conn.execute(text(
                    "SELECT id, extra_data, oauth_recovery_required, openai_auth_state, token_validation_state, openai_account_state "
                    "FROM accounts"
                ))
                rows = result.fetchall()
                for row in rows:
                    acc_id = row[0]
                    extra_raw = row[1]
                    current_oauth = row[2]
                    current_auth = row[3]
                    current_token = row[4]
                    current_account = row[5]
                    try:
                        extra = json.loads(extra_raw) if extra_raw else {}
                    except Exception:
                        extra = {}
                    if not isinstance(extra, dict):
                        extra = {}

                    updates = {}
                    if current_oauth is None and "oauth_recovery_required" in extra:
                        updates["oauth_recovery_required"] = 1 if bool(extra.get("oauth_recovery_required")) else 0
                    if not current_auth and extra.get("openai_auth_state"):
                        updates["openai_auth_state"] = str(extra.get("openai_auth_state") or "")
                    if not current_token and extra.get("token_validation_state"):
                        updates["token_validation_state"] = str(extra.get("token_validation_state") or "")
                    if not current_account and extra.get("openai_account_state"):
                        updates["openai_account_state"] = str(extra.get("openai_account_state") or "")
                    if updates:
                        set_clause = ", ".join([f"{key} = :{key}" for key in updates.keys()])
                        conn.execute(
                            text(f"UPDATE accounts SET {set_clause} WHERE id = :acc_id"),
                            {"acc_id": acc_id, **updates}
                        )
                conn.commit()
            except Exception as e:
                logger.warning(f"回填 accounts 高频状态列时出错: {e}")


# 全局数据库会话管理器实例
_db_manager: DatabaseSessionManager = None


def init_database(database_url: str = None) -> DatabaseSessionManager:
    """
    初始化数据库会话管理器
    """
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseSessionManager(database_url)
        _db_manager.create_tables()
        # 执行数据库迁移
        _db_manager.migrate_tables()
    return _db_manager


def get_session_manager() -> DatabaseSessionManager:
    """
    获取数据库会话管理器
    """
    if _db_manager is None:
        raise RuntimeError("数据库未初始化，请先调用 init_database()")
    return _db_manager


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """
    获取数据库会话的快捷函数
    """
    manager = get_session_manager()
    db = manager.SessionLocal()
    try:
        yield db
    finally:
        db.close()
