"""SQLAlchemy models. PostgreSQL (+pgvector) in production, SQLite for tests/dev."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, create_engine, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .core.config import settings

IS_PG = settings.database_url.startswith("postgresql")
JsonType = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(18, 4, asdecimal=False)
try:
    from pgvector.sqlalchemy import Vector
    EmbType = Vector(settings.embedding_dim) if IS_PG else JSON
except ImportError:  # pragma: no cover
    EmbType = JSON

now = lambda: datetime.now(timezone.utc)
uid = lambda: uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    filename: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(String(1000))
    kind: Mapped[str | None] = mapped_column(String(10))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="queued")  # queued|processing|retrying|completed|needs_review|failed
    stage: Mapped[str | None] = mapped_column(String(100))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    pages_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_of: Mapped[str | None] = mapped_column(String(32))
    warnings: Mapped[list] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    pages = relationship("Page", cascade="all, delete-orphan", order_by="Page.page_no")
    invoices = relationship("Invoice", cascade="all, delete-orphan", order_by="Invoice.seq")
    issues = relationship("ValidationIssue", cascade="all, delete-orphan")
    events = relationship("ProcessingEvent", cascade="all, delete-orphan", order_by="ProcessingEvent.id")


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(15))
    ocr_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4, asdecimal=False))
    error: Mapped[str | None] = mapped_column(Text)


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(EmbType)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(15))
    invoice_number: Mapped[str | None] = mapped_column(String(100), index=True)
    invoice_date: Mapped[str | None] = mapped_column(String(10), index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(300))
    supplier_norm: Mapped[str | None] = mapped_column(String(300), index=True)
    customer_name: Mapped[str | None] = mapped_column(String(300))
    subtotal: Mapped[float | None] = mapped_column(Money)
    tax: Mapped[float | None] = mapped_column(Money)
    total: Mapped[float | None] = mapped_column(Money, index=True)
    currency: Mapped[str | None] = mapped_column(String(3))
    confidence: Mapped[dict] = mapped_column(JsonType, default=dict)
    notes: Mapped[list] = mapped_column(JsonType, default=list)
    extraction_method: Mapped[str | None] = mapped_column(String(20))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    manually_corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    line_items = relationship("LineItem", cascade="all, delete-orphan", order_by="LineItem.position")
    __table_args__ = (Index("ix_invoice_dedupe", "supplier_norm", "invoice_number"),)


class LineItem(Base):
    __tablename__ = "line_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[float | None] = mapped_column(Money)
    unit_price: Mapped[float | None] = mapped_column(Money)
    amount: Mapped[float | None] = mapped_column(Money)


class ValidationIssue(Base):
    __tablename__ = "validation_issues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    field: Mapped[str | None] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProcessingEvent(Base):
    __tablename__ = "processing_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Correction(Base):
    __tablename__ = "corrections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(40))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    user: Mapped[str] = mapped_column(String(100), default="reviewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    if IS_PG:
        with engine.begin() as c:
            try:
                c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            except Exception:
                pass
    Base.metadata.create_all(engine)
    if IS_PG:  # full-text + ANN indexes (idempotent)
        with engine.begin() as c:
            c.execute(text("CREATE INDEX IF NOT EXISTS ix_pages_fts ON pages USING GIN (to_tsvector('simple', text))"))
            try:
                c.execute(text("CREATE INDEX IF NOT EXISTS ix_chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"))
            except Exception:
                pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
