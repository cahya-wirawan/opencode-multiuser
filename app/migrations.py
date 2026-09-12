import logging

from sqlalchemy import inspect, text

from .db import engine

logger = logging.getLogger(__name__)

LEGACY_CONSTRAINT = "uq_activeish_workspace"
ACTIVE_INDEX = "uq_active_workspace"
OIDC_INDEX = "uq_oidc_identity"


def migrate_user_auth_schema() -> None:
    """Upgrade v6.5 and older users tables for roles and generic OIDC.

    Production uses PostgreSQL, where password_hash is made nullable for OIDC-only
    accounts. Fresh SQLite databases are created directly from the current model;
    legacy SQLite receives additive columns but keeps its historic NOT NULL rule.
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "postgresql":
            statements = [
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20)",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20)",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS oidc_issuer VARCHAR(512)",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS oidc_subject VARCHAR(512)",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(320)",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(160)",
                "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL",
            ]
            for statement in statements:
                conn.exec_driver_sql(statement)
        elif dialect == "sqlite":
            existing = {col["name"] for col in inspect(conn).get_columns("users")}
            additions = {
                "role": "VARCHAR(20)",
                "auth_provider": "VARCHAR(20)",
                "oidc_issuer": "VARCHAR(512)",
                "oidc_subject": "VARCHAR(512)",
                "email": "VARCHAR(320)",
                "display_name": "VARCHAR(160)",
            }
            for name, sql_type in additions.items():
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE users ADD COLUMN {name} {sql_type}")
        else:
            logger.warning("Unsupported dialect %s; user auth schema migration skipped", dialect)
            return

        conn.execute(text("UPDATE users SET role='developer' WHERE role IS NULL OR role=''"))
        conn.execute(text("UPDATE users SET auth_provider='local' WHERE auth_provider IS NULL OR auth_provider=''"))
        conn.execute(
            text(
                """UPDATE users SET role='admin'
                   WHERE id = (SELECT id FROM users ORDER BY created_at ASC, id ASC LIMIT 1)
                     AND NOT EXISTS (SELECT 1 FROM users WHERE role='admin')"""
            )
        )
        if dialect == "postgresql":
            conn.exec_driver_sql("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'developer'")
            conn.exec_driver_sql("ALTER TABLE users ALTER COLUMN role SET NOT NULL")
            conn.exec_driver_sql("ALTER TABLE users ALTER COLUMN auth_provider SET DEFAULT 'local'")
            conn.exec_driver_sql("ALTER TABLE users ALTER COLUMN auth_provider SET NOT NULL")

        conn.execute(
            text(
                f'''CREATE UNIQUE INDEX IF NOT EXISTS "{OIDC_INDEX}"
                    ON users (oidc_issuer, oidc_subject)
                    WHERE oidc_subject IS NOT NULL'''
            )
        )
    logger.info("User auth schema is ready for local roles and OIDC")


def drop_legacy_workspace_constraint() -> None:
    dialect = engine.dialect.name
    if dialect == "postgresql":
        with engine.begin() as conn:
            conn.exec_driver_sql(
                f'ALTER TABLE IF EXISTS workspaces DROP CONSTRAINT IF EXISTS "{LEGACY_CONSTRAINT}"'
            )
        logger.info("Dropped legacy workspace constraint if present: %s", LEGACY_CONSTRAINT)
    elif dialect == "sqlite":
        logger.info("SQLite detected; skipping in-place legacy constraint drop")


def ensure_active_workspace_index() -> None:
    dialect = engine.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        logger.warning("Unsupported dialect %s; active workspace index not created", dialect)
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                f'''CREATE UNIQUE INDEX IF NOT EXISTS "{ACTIVE_INDEX}"
                    ON workspaces (user_id, project_slug)
                    WHERE status IN ('STARTING','RUNNING','STOPPING')'''
            )
        )
    logger.info("Ensured partial active-workspace index: %s", ACTIVE_INDEX)
