"""Orchestration between the pure pipeline and the database."""
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .core.embeddings import chunk_text, embed
from .core.errors import DocIntelError, TransientError
from .core.pipeline import process_file
from .core.utils import norm_invoice_no, norm_name
from .core.validation import needs_review, validate_invoice
from .db import (Chunk, Document, Invoice, LineItem, Page, ProcessingEvent, SessionLocal, ValidationIssue, now)

log = logging.getLogger("docintel")
INVOICE_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency")


def log_event(db: Session, doc: Document, stage: str, status: str, message: str = "", ms: Optional[int] = None):
    db.add(ProcessingEvent(document_id=doc.id, stage=stage, status=status, message=message[:2000], duration_ms=ms))


def invoice_dict(inv: Invoice) -> Dict:
    d = {k: getattr(inv, k) for k in INVOICE_FIELDS}
    d.update(id=inv.id, document_id=inv.document_id, confidence=inv.confidence or {}, notes=inv.notes or [],
             line_items=[{"description": li.description, "quantity": li.quantity, "unit_price": li.unit_price, "amount": li.amount}
                         for li in inv.line_items])
    return d


def known_duplicates(db: Session, inv: Invoice) -> List[Dict]:
    """Earlier invoices that could be the same document (index-backed lookup)."""
    older = or_(Invoice.created_at < inv.created_at, and_(Invoice.created_at == inv.created_at, Invoice.id < inv.id))
    conds = []
    if inv.invoice_number:
        conds.append(Invoice.invoice_number == inv.invoice_number)
    if inv.supplier_norm and inv.total is not None and inv.invoice_date:
        conds.append(and_(Invoice.supplier_norm == inv.supplier_norm, Invoice.total == inv.total, Invoice.invoice_date == inv.invoice_date))
    if not conds:
        return []
    rows = db.scalars(select(Invoice).where(Invoice.id != inv.id, older, or_(*conds)).limit(20)).all()
    return [dict(id=r.id, invoice_number=r.invoice_number, supplier_name=r.supplier_name, total=r.total, invoice_date=r.invoice_date)
            for r in rows]


def revalidate_invoice(db: Session, inv: Invoice) -> bool:
    """Replace the invoice's validation issues; returns needs_review."""
    for i in db.scalars(select(ValidationIssue).where(ValidationIssue.invoice_id == inv.id)).all():
        db.delete(i)
    db.flush()
    issues = validate_invoice(invoice_dict(inv), known_duplicates(db, inv))
    for i in issues:
        db.add(ValidationIssue(document_id=inv.document_id, invoice_id=inv.id, code=i.code, field=i.field,
                               severity=i.severity, message=i.message))
    inv.needs_review = needs_review(issues) and not inv.reviewed
    return inv.needs_review


def refresh_document_status(db: Session, doc: Document) -> None:
    db.flush()
    invs = db.scalars(select(Invoice).where(Invoice.document_id == doc.id)).all()
    doc_level = db.scalars(select(ValidationIssue).where(ValidationIssue.document_id == doc.id, ValidationIssue.invoice_id.is_(None))).all()
    doc.status = "needs_review" if (any(i.needs_review for i in invs) or doc_level) else "completed"


def _clear_previous(db: Session, doc: Document) -> None:
    for model in (ValidationIssue, Chunk, Invoice, Page):
        for row in db.scalars(select(model).where(model.document_id == doc.id)).all():
            db.delete(row)
    db.flush()


def process_document(doc_id: str, attempt: int = 0) -> None:
    """Runs inside the worker. PERMANENT errors -> failed. Transient errors are re-raised for the queue to retry."""
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            return
        t0 = time.perf_counter()
        doc.status, doc.progress, doc.error, doc.attempts = "processing", 1, None, attempt + 1
        doc.stage = "starting"
        log_event(db, doc, "process", "started", f"attempt {attempt + 1}")
        db.commit()
        last = [0.0]

        def progress(pct: int, stage: str):
            if time.perf_counter() - last[0] > 0.7 or pct >= 95:  # throttle DB writes
                last[0] = time.perf_counter()
                doc.progress, doc.stage = min(pct, 99), stage
                db.commit()

        try:
            result = process_file(doc.stored_path, doc.filename, progress=progress)
        except TransientError as e:
            doc.status, doc.error = "retrying", str(e)
            log_event(db, doc, "process", "retrying", str(e))
            db.commit()
            raise
        except DocIntelError as e:
            _fail(db, doc, f"{type(e).__name__}: {e}", t0)
            return
        except Exception as e:  # unknown bug: fail the job, keep the worker alive
            log.exception("unexpected failure for %s", doc_id)
            _fail(db, doc, f"UnexpectedError: {e}", t0)
            return

        _clear_previous(db, doc)
        doc.kind, doc.pages_count, doc.warnings = result.kind, len(result.pages), result.warnings
        for p in result.pages:
            db.add(Page(document_id=doc.id, page_no=p.page_no, text=p.text, method=p.method, ocr_confidence=p.confidence, error=p.error))
        chunks = [(p.page_no, c) for p in result.pages for c in chunk_text(p.text)]
        for i in range(0, len(chunks), 64):  # batch embedding
            batch = chunks[i:i + 64]
            for (pn, txt), vec in zip(batch, embed([c for _, c in batch])):
                db.add(Chunk(document_id=doc.id, page_no=pn, text=txt, embedding=vec))
        base = now()
        for seq, d in enumerate(result.invoices):
            inv = Invoice(document_id=doc.id, seq=seq, page_start=d.get("page_start"), page_end=d.get("page_end"), source=d.get("source"),
                          supplier_norm=norm_name(d.get("supplier_name")), confidence=d.get("confidence") or {}, notes=d.get("notes") or [],
                          extraction_method=d.get("method"), created_at=base + timedelta(microseconds=seq),
                          **{k: d.get(k) for k in INVOICE_FIELDS})
            for pos, li in enumerate(d.get("line_items") or []):
                inv.line_items.append(LineItem(position=pos, **li))
            if d.get("invoice_date_raw") and not inv.invoice_date:
                inv.notes = (inv.notes or []) + [f"unparseable date: {d['invoice_date_raw']}"]
            db.add(inv)
            db.flush()
            revalidate_invoice(db, inv)
        for i in result.doc_issues:
            db.add(ValidationIssue(document_id=doc.id, code=i.code, field=i.field, severity=i.severity, message=i.message))
        refresh_document_status(db, doc)
        doc.progress, doc.stage, doc.error = 100, "done", None
        doc.finished_at = now()
        doc.processing_ms = int((time.perf_counter() - t0) * 1000)
        log_event(db, doc, "process", doc.status, f"{len(result.invoices)} invoice(s), {len(result.pages)} page(s)", doc.processing_ms)
        db.commit()
    finally:
        db.close()


def _fail(db: Session, doc: Document, msg: str, t0: float) -> None:
    doc.status, doc.error, doc.stage = "failed", msg[:2000], "failed"
    doc.finished_at = now()
    doc.processing_ms = int((time.perf_counter() - t0) * 1000)
    log_event(db, doc, "process", "failed", msg, doc.processing_ms)
    db.commit()


def mark_failed(doc_id: str, msg: str) -> None:
    """Called by the queue when all retries are exhausted."""
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc:
            _fail(db, doc, msg, time.perf_counter())
    finally:
        db.close()


def process_document_safe(doc_id: str) -> None:
    """Inline (no-Redis) runner: retries transient errors with backoff in-process."""
    for attempt in range(4):
        try:
            return process_document(doc_id, attempt)
        except TransientError as e:
            if attempt == 3:
                return mark_failed(doc_id, f"Gave up after retries: {e}")
            time.sleep(min(2 ** attempt, 8))
