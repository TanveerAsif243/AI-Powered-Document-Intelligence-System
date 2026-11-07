"""REST API. All /api routes require the X-API-Key header."""
import hashlib
import os
import uuid
from datetime import date
from typing import List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import search, services
from .core.config import settings
from .core.detect import ext_kind
from .core.reconcile import reconcile
from .core.utils import norm_name, normalize_currency, parse_amount, parse_date
from .db import Correction, Document, Invoice, LineItem, Page, ValidationIssue, get_db, init_db, now
from .security import require_api_key

app = FastAPI(title="Document Intelligence API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

from fastapi import APIRouter
router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


@app.on_event("startup")
def _startup():
    os.makedirs(settings.upload_dir, exist_ok=True)
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


def enqueue(doc_id: str, background: BackgroundTasks) -> None:
    if settings.inline_jobs:
        background.add_task(services.process_document_safe, doc_id)
    else:
        from .tasks import process_document_task
        process_document_task.delay(doc_id)


# ---------- serializers ----------
def doc_out(d: Document) -> dict:
    return {"id": d.id, "filename": d.filename, "kind": d.kind, "size_bytes": d.size_bytes, "status": d.status,
            "progress": d.progress, "stage": d.stage, "error": d.error, "attempts": d.attempts, "pages": d.pages_count,
            "duplicate_of": d.duplicate_of, "warnings": d.warnings or [], "processing_ms": d.processing_ms,
            "created_at": d.created_at, "finished_at": d.finished_at}


def invoice_out(db: Session, inv: Invoice) -> dict:
    out = services.invoice_dict(inv)
    out.update(seq=inv.seq, page_start=inv.page_start, page_end=inv.page_end, source=inv.source, needs_review=inv.needs_review,
               reviewed=inv.reviewed, manually_corrected=inv.manually_corrected, extraction_method=inv.extraction_method,
               issues=[{"code": i.code, "severity": i.severity, "field": i.field, "message": i.message}
                       for i in db.scalars(select(ValidationIssue).where(ValidationIssue.invoice_id == inv.id)).all()])
    return out


def get_doc(db: Session, doc_id: str) -> Document:
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    return d


# ---------- upload ----------
@router.post("/documents", status_code=202)
async def upload(background: BackgroundTasks, files: List[UploadFile] = File(...), db: Session = Depends(get_db)):
    if len(files) > settings.max_files_per_request:
        raise HTTPException(413, f"Too many files (max {settings.max_files_per_request} per request)")
    accepted, rejected, limit = [], [], settings.max_upload_mb * 1024 * 1024
    os.makedirs(settings.upload_dir, exist_ok=True)
    for f in files:
        name = os.path.basename(f.filename or "upload")
        ext = os.path.splitext(name)[1].lower()
        if not ext_kind(name):
            rejected.append({"filename": name, "reason": f"Unsupported extension '{ext}'. Allowed: pdf, jpg, png, tiff, xlsx, xls, csv"})
            continue
        path = os.path.join(settings.upload_dir, f"{uuid.uuid4().hex}{ext}")  # never trust client file names for paths
        h, size, too_big = hashlib.sha256(), 0, False
        with open(path, "wb") as out:
            while chunk := await f.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    too_big = True
                    break
                h.update(chunk)
                out.write(chunk)
        if too_big:
            os.remove(path)
            rejected.append({"filename": name, "reason": f"File exceeds {settings.max_upload_mb} MB"})
            continue
        if size == 0:
            os.remove(path)
            rejected.append({"filename": name, "reason": "File is empty"})
            continue
        digest = h.hexdigest()
        prior = db.scalars(select(Document).where(Document.sha256 == digest, Document.status != "failed").order_by(Document.created_at)).first()
        doc = Document(filename=name, stored_path=path, size_bytes=size, sha256=digest, status="queued",
                       duplicate_of=prior.id if prior else None)
        db.add(doc)
        db.flush()
        services.log_event(db, doc, "upload", "queued", "duplicate of " + prior.id if prior else "")
        db.commit()
        enqueue(doc.id, background)
        accepted.append(doc_out(doc))
    if not accepted and rejected:
        raise HTTPException(400, {"message": "No files accepted", "rejected": rejected})
    return {"documents": accepted, "rejected": rejected}


# ---------- documents ----------
@router.get("/documents")
def list_documents(status: Optional[str] = None, limit: int = Query(50, le=200), offset: int = 0, db: Session = Depends(get_db)):
    q = select(Document)
    if status:
        q = q.where(Document.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()
    counts = dict(db.execute(select(Document.status, func.count()).group_by(Document.status)).all())
    return {"total": total, "counts": counts, "items": [doc_out(d) for d in rows]}


@router.get("/documents/{doc_id}")
def document_detail(doc_id: str, db: Session = Depends(get_db)):
    d = get_doc(db, doc_id)
    out = doc_out(d)
    out["events"] = [{"stage": e.stage, "status": e.status, "message": e.message, "duration_ms": e.duration_ms, "at": e.created_at} for e in d.events]
    out["document_issues"] = [{"code": i.code, "severity": i.severity, "message": i.message}
                              for i in d.issues if i.invoice_id is None]
    return out


@router.get("/documents/{doc_id}/status")
def document_status(doc_id: str, db: Session = Depends(get_db)):
    d = get_doc(db, doc_id)
    return {"id": d.id, "status": d.status, "progress": d.progress, "stage": d.stage, "attempts": d.attempts, "error": d.error}


@router.get("/documents/{doc_id}/result")
def document_result(doc_id: str, db: Session = Depends(get_db)):
    d = get_doc(db, doc_id)
    if d.status in ("queued", "processing", "retrying"):
        raise HTTPException(409, f"Document is still {d.status}")
    return {"document": doc_out(d), "invoices": [invoice_out(db, i) for i in d.invoices],
            "document_issues": [{"code": i.code, "severity": i.severity, "message": i.message} for i in d.issues if i.invoice_id is None]}


@router.get("/documents/{doc_id}/text")
def document_text(doc_id: str, page: Optional[int] = None, db: Session = Depends(get_db)):
    get_doc(db, doc_id)
    q = select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)
    if page:
        q = q.where(Page.page_no == page)
    return [{"page": p.page_no, "method": p.method, "ocr_confidence": p.ocr_confidence, "text": p.text} for p in db.scalars(q.limit(200)).all()]


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: str, db: Session = Depends(get_db)):
    d = get_doc(db, doc_id)
    if not os.path.exists(d.stored_path):
        raise HTTPException(404, "Original file no longer available")
    return FileResponse(d.stored_path, filename=d.filename)


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    d = get_doc(db, doc_id)
    path = d.stored_path
    db.delete(d)
    db.commit()
    if os.path.exists(path):
        os.remove(path)


@router.post("/documents/{doc_id}/reprocess", status_code=202)
def reprocess(doc_id: str, background: BackgroundTasks, force: bool = False, db: Session = Depends(get_db)):
    d = get_doc(db, doc_id)
    if d.status in ("queued", "processing", "retrying"):
        raise HTTPException(409, "Document is already being processed")
    if d.status != "failed" and not force:
        raise HTTPException(409, "Only failed documents can be reprocessed (use force=true to override)")
    d.status, d.progress, d.error, d.stage = "queued", 0, None, "queued"
    services.log_event(db, d, "reprocess", "queued", "manual reprocess")
    db.commit()
    enqueue(d.id, background)
    return doc_out(d)


@router.post("/documents/reprocess-failed", status_code=202)
def reprocess_failed(background: BackgroundTasks, db: Session = Depends(get_db)):
    ids = []
    for d in db.scalars(select(Document).where(Document.status == "failed")).all():
        d.status, d.progress, d.error, d.stage = "queued", 0, None, "queued"
        services.log_event(db, d, "reprocess", "queued", "bulk reprocess of failed jobs")
        ids.append(d.id)
    db.commit()
    for i in ids:
        enqueue(i, background)
    return {"requeued": ids}


# ---------- invoices: manual correction ----------
class LineItemIn(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class InvoicePatch(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    supplier_name: Optional[str] = None
    customer_name: Optional[str] = None
    subtotal: Optional[float] = Field(None, ge=-1e12, le=1e12)
    tax: Optional[float] = Field(None, ge=-1e12, le=1e12)
    total: Optional[float] = Field(None, ge=-1e12, le=1e12)
    currency: Optional[str] = None
    line_items: Optional[List[LineItemIn]] = None
    approve: bool = False                       # reviewer accepts the invoice as-is despite warnings
    user: str = "reviewer"


@router.get("/invoices/{inv_id}")
def get_invoice(inv_id: str, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return invoice_out(db, inv)


@router.patch("/invoices/{inv_id}")
def correct_invoice(inv_id: str, body: InvoicePatch, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    changes = body.model_dump(exclude_unset=True, exclude={"approve", "user", "line_items"})
    if "invoice_date" in changes and changes["invoice_date"] not in (None, ""):
        iso = parse_date(changes["invoice_date"])
        if not iso:
            raise HTTPException(422, "invoice_date must be a valid date (YYYY-MM-DD)")
        changes["invoice_date"] = iso
    if "currency" in changes and changes["currency"]:
        cur = normalize_currency(changes["currency"])
        if not cur:
            raise HTTPException(422, "currency must be a 3-letter ISO code")
        changes["currency"] = cur
    for f, new in changes.items():
        old = getattr(inv, f)
        new = None if new == "" else new
        if old != new:
            db.add(Correction(invoice_id=inv.id, field=f, old_value=None if old is None else str(old), new_value=None if new is None else str(new), user=body.user))
            setattr(inv, f, new)
            inv.confidence = {**(inv.confidence or {}), f: 1.0}   # human-verified
    if "supplier_name" in changes:
        inv.supplier_norm = norm_name(inv.supplier_name)
    if body.line_items is not None:
        db.add(Correction(invoice_id=inv.id, field="line_items", old_value=str(len(inv.line_items)), new_value=str(len(body.line_items)), user=body.user))
        inv.line_items.clear()
        db.flush()
        for pos, li in enumerate(body.line_items):
            inv.line_items.append(LineItem(position=pos, **li.model_dump()))
    inv.manually_corrected = inv.manually_corrected or bool(changes) or body.line_items is not None
    inv.reviewed = bool(body.approve)
    services.revalidate_invoice(db, inv)
    services.refresh_document_status(db, db.get(Document, inv.document_id))
    db.commit()
    return invoice_out(db, inv)


@router.get("/invoices/{inv_id}/corrections")
def invoice_corrections(inv_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(Correction).where(Correction.invoice_id == inv_id).order_by(Correction.id)).all()
    return [{"field": c.field, "old": c.old_value, "new": c.new_value, "user": c.user, "at": c.created_at} for c in rows]


# ---------- search ----------
@router.get("/search")
def search_documents(q: str = "", mode: str = Query("keyword", pattern="^(keyword|semantic)$"), status: Optional[str] = None,
                     kind: Optional[str] = None, supplier: Optional[str] = None, customer: Optional[str] = None,
                     invoice_number: Optional[str] = None, currency: Optional[str] = None, date_from: Optional[date] = None,
                     date_to: Optional[date] = None, min_total: Optional[float] = None, max_total: Optional[float] = None,
                     needs_review: Optional[bool] = None, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                     db: Session = Depends(get_db)):
    filters = dict(status=status, kind=kind, supplier=supplier, customer=customer, invoice_number=invoice_number, currency=currency,
                   date_from=date_from.isoformat() if date_from else None, date_to=date_to.isoformat() if date_to else None,
                   min_total=min_total, max_total=max_total, needs_review=needs_review)
    if mode == "semantic":
        if not q.strip():
            raise HTTPException(422, "Semantic search needs a query")
        results = search.semantic_search(db, q, filters, limit, offset)
    else:
        results = search.keyword_search(db, q, filters, limit, offset)
    return {"mode": mode, "query": q, "results": results}


# ---------- cross verification (PDF-PDF, PDF-Excel) ----------
@router.get("/reconcile")
def reconcile_documents(left: str, right: str, db: Session = Depends(get_db)):
    sides = []
    for did in (left, right):
        get_doc(db, did)
        sides.append([services.invoice_dict(i) for i in db.scalars(select(Invoice).where(Invoice.document_id == did)).all()])
    return reconcile(sides[0], sides[1])


app.include_router(router)
