import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, Enum as SAEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base
import enum

class ManuscriptStatus(str, enum.Enum):
    received = "received"
    processing = "processing"
    done = "done"
    error = "error"

class EditorStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    revision_requested = "revision_requested"

class Priority(str, enum.Enum):
    normal = "normal"
    high = "high"

class StageStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    done = "done"
    retrying = "retrying"
    error = "error"

class Manuscript(Base):
    __tablename__ = "manuscripts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: "m_" + str(uuid.uuid4())[:8])
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    author_email: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_attention: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[ManuscriptStatus] = mapped_column(SAEnum(ManuscriptStatus), default=ManuscriptStatus.received)
    editor_status: Mapped[EditorStatus] = mapped_column(SAEnum(EditorStatus), default=EditorStatus.pending)
    priority: Mapped[Priority] = mapped_column(SAEnum(Priority), default=Priority.normal)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    stages: Mapped[list["ManuscriptStage"]] = relationship("ManuscriptStage", back_populates="manuscript", cascade="all, delete-orphan")

class ManuscriptStage(Base):
    __tablename__ = "manuscript_stages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    manuscript_id: Mapped[str] = mapped_column(String, ForeignKey("manuscripts.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[StageStatus] = mapped_column(SAEnum(StageStatus), default=StageStatus.pending)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    manuscript: Mapped["Manuscript"] = relationship("Manuscript", back_populates="stages")

class NotificationKey(Base):
    __tablename__ = "notification_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
