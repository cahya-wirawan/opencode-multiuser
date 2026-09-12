import logging

from sqlalchemy import text

from .db import engine

logger = logging.getLogger(__name__)


LEGACY_CONSTRAINT = "uq_activeish_workspace"
ACTIVE_INDEX = "uq_active_workspace"


def drop_legacy_workspace_constraint() -> None:
    """Remove the v1-v6.4.2 constraint that incorrectly made STOPPED/ERROR unique.

    PostgreSQL can drop this constraint in place. Fresh SQLite databases use the
    corrected partial index from model metadata; in-place migration of a legacy
    SQLite table would require table recreation and is intentionally not attempted
    because the production deployment uses PostgreSQL.
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        with engine.begin() as conn:
            conn.exec_driver_sql(
                f'ALTER TABLE IF EXISTS workspaces DROP CONSTRAINT IF EXISTS "{LEGACY_CONSTRAINT}"'
            )
        logger.info("Dropped legacy workspace constraint if present: %s", LEGACY_CONSTRAINT)
    elif dialect == "sqlite":
        # SQLite cannot DROP CONSTRAINT. A fresh database is already correct.
        logger.info("SQLite detected; skipping in-place legacy constraint drop")


def ensure_active_workspace_index() -> None:
    """Ensure at most one active runtime lease per user/project.

    Terminal states are excluded so any number of STOPPED/ERROR historical rows
    can coexist without blocking reconciliation or shutdown.
    """
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
