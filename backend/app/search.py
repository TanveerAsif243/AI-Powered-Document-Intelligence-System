"""Keyword (full-text) and semantic (vector) search with structured filters."""
from typing import Dict, List, Optional

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from .core.embeddings import cosine, embed
from .db import IS_PG, Chunk, Document, Invoice, Page


def _filters(f: Dict):
    conds = []
    if f.get("status"):
        conds.append(Document.status == f["status"])
    if f.get("kind"):
        conds.append(Document.kind == f["kind"])
    inv = []
    if f.get("supplier"):
        inv.append(Invoice.supplier_name.ilike(f"%{f['supplier']}%"))
    if f.get("customer"):
        inv.append(Invoice.customer_name.ilike(f"%{f['customer']}%"))
    if f.get("invoice_number"):
        inv.append(Invoice.invoice_number.ilike(f"%{f['invoice_number']}%"))
    if f.get("currency"):
        inv.append(Invoice.currency == f["currency"].upper())
    if f.get("date_from"):
        inv.append(Invoice.invoice_date >= f["date_from"])
    if f.get("date_to"):
        inv.append(Invoice.invoice_date <= f["date_to"])
    if f.get("min_total") is not None:
        inv.append(Invoice.total >= f["min_total"])
    if f.get("max_total") is not None:
        inv.append(Invoice.total <= f["max_total"])
    if f.get("needs_review") is not None:
        inv.append(Invoice.needs_review == f["needs_review"])
    if inv:
        conds.append(exists().where(and_(Invoice.document_id == Document.id, *inv)))
    return conds


def _summary(db: Session, doc: Document, score: float, snippet: Optional[str]) -> Dict:
    invs = db.scalars(select(Invoice).where(Invoice.document_id == doc.id).limit(5)).all()
    return {"document_id": doc.id, "filename": doc.filename, "status": doc.status, "score": round(score, 4),
            "snippet": (snippet or "")[:300],
            "invoices": [{"id": i.id, "invoice_number": i.invoice_number, "supplier_name": i.supplier_name,
                          "total": i.total, "currency": i.currency, "needs_review": i.needs_review} for i in invs]}


def keyword_search(db: Session, q: str, filters: Dict, limit: int, offset: int) -> List[Dict]:
    conds = _filters(filters)
    if q:
        like = f"%{q}%"
        inv_hit = exists().where(and_(Invoice.document_id == Document.id, or_(
            Invoice.invoice_number.ilike(like), Invoice.supplier_name.ilike(like), Invoice.customer_name.ilike(like))))
        if IS_PG:
            tsq = func.websearch_to_tsquery("simple", q)
            page_hit = exists().where(and_(Page.document_id == Document.id, func.to_tsvector("simple", Page.text).op("@@")(tsq)))
        else:
            page_hit = exists().where(and_(Page.document_id == Document.id, Page.text.ilike(like)))
        conds.append(or_(Document.filename.ilike(like), inv_hit, page_hit))
    docs = db.scalars(select(Document).where(*conds).order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()
    out = []
    for d in docs:
        snip = None
        if q:
            p = db.scalars(select(Page).where(Page.document_id == d.id, Page.text.ilike(f"%{q}%")).limit(1)).first()
            if p:
                i = p.text.lower().find(q.lower())
                snip = p.text[max(0, i - 80): i + 220]
        out.append(_summary(db, d, 1.0, snip))
    return out


def semantic_search(db: Session, q: str, filters: Dict, limit: int, offset: int) -> List[Dict]:
    qv = embed([q])[0]
    conds = _filters(filters)
    if IS_PG:
        dist = Chunk.embedding.cosine_distance(qv)
        rows = db.execute(select(Chunk.document_id, Chunk.text, dist.label("d")).join(Document, Document.id == Chunk.document_id)
                          .where(*conds).order_by(dist).limit(limit * 5)).all()
        scored = [(r.document_id, 1 - r.d, r.text) for r in rows]
    else:  # portable fallback for SQLite (dev/tests)
        rows = db.execute(select(Chunk.document_id, Chunk.text, Chunk.embedding).join(Document, Document.id == Chunk.document_id).where(*conds)).all()
        scored = [(r.document_id, cosine(qv, r.embedding), r.text) for r in rows]
    best: Dict[str, tuple] = {}
    for did, s, t in scored:
        if did not in best or s > best[did][0]:
            best[did] = (s, t)
    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[offset: offset + limit]
    return [_summary(db, db.get(Document, did), s, t) for did, (s, t) in ranked if s > 0.02]
