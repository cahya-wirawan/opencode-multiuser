import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now_utc():
    return datetime.now(timezone.utc)


class WorkspaceStatus(str, enum.Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Slot(Base):
    __tablename__ = "slots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    in_use: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Workspace(Base):
    __tablename__ = "workspaces"
    # Only active lifecycle states must be unique. Historical STOPPED/ERROR rows
    # are intentionally allowed so runtime reconciliation can preserve history.
    __table_args__ = (
        Index(
            "uq_active_workspace",
            "user_id",
            "project_slug",
            unique=True,
            postgresql_where=text("status IN ('STARTING','RUNNING','STOPPING')"),
            sqlite_where=text("status IN ('STARTING','RUNNING','STOPPING')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_slug: Mapped[str] = mapped_column(String(80), index=True)
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("slots.id"), nullable=True)
    container_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[WorkspaceStatus] = mapped_column(Enum(WorkspaceStatus), default=WorkspaceStatus.STARTING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship()
    slot: Mapped[Slot | None] = relationship()
