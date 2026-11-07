"""File -> pages -> invoices. Pure (no DB): used by the worker, the tests and the evaluation harness."""
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import ocr as _ocr, pdf as _pdf, spreadsheet as _sheet
from .config import settings
from .detect import detect_kind
from .extract import extract_invoice
from .models import FIELDS, Issue, PageResult
from .segment import segment_pages


@dataclass
class ProcessedDocument:
    kind: str
    pages: List[PageResult]
    invoices: List[dict]
    warnings: List[str] = field(default_factory=list)
    doc_issues: List[Issue] = field(default_factory=list)


Progress = Optional[Callable[[int, str], None]]


def _scale_confidence(inv: dict, pages: List[PageResult]) -> None:
    """Poor OCR must lower field confidence so shaky values get flagged for review."""
    confs = [p.confidence for p in pages if p.method == "ocr" and p.confidence is not None]
    if not confs:
        return
    mean = sum(confs) / len(confs)
    factor = max(0.4, min(1.0, mean / 0.85))
    inv["confidence"] = {k: round(v * factor, 3) for k, v in inv["confidence"].items()}
    inv["ocr_confidence"] = round(mean, 3)


def process_file(path: str, filename: str, use_llm: Optional[bool] = None, progress: Progress = None) -> ProcessedDocument:
    report = progress or (lambda pct, stage: None)
    report(2, "detecting file type")
    kind, warnings = detect_kind(path, filename)
    invoices: List[dict] = []
    doc_issues: List[Issue] = []

    if kind in ("pdf", "image"):
        report(5, "extracting text")
        if kind == "pdf":
            pages = _pdf.extract_pdf(path, lambda done, total: report(5 + int(55 * done / total), "extracting text / OCR"))
        else:
            pages = _ocr.extract_image(path)
        report(62, "locating invoices")
        for p in pages:
            if p.method == "failed":
                warnings.append(f"Page {p.page_no}: {p.error}")
            elif p.method == "ocr" and p.confidence is not None and p.confidence < settings.low_ocr_confidence:
                warnings.append(f"Page {p.page_no}: low OCR confidence ({p.confidence:.2f}); image quality is poor")
        segments = segment_pages(pages)
        for n, seg in enumerate(segments, start=1):
            report(62 + int(33 * n / max(1, len(segments))), f"extracting invoice {n}/{len(segments)}")
            inv = extract_invoice(seg.text, use_llm)
            inv.update(page_start=seg.start, page_end=seg.end,
                       source="ocr" if all(p.method == "ocr" for p in seg.pages) else
                       ("text" if all(p.method == "text" for p in seg.pages) else "mixed"))
            _scale_confidence(inv, seg.pages)
            invoices.append(inv)
    else:
        report(10, "parsing spreadsheet")
        sheets = _sheet.parse_spreadsheet(path, kind)
        pages = [PageResult(i, s.text, "spreadsheet", 1.0) for i, s in enumerate(sheets, start=1)]
        for i, s in enumerate(sheets, start=1):
            report(10 + int(85 * i / len(sheets)), f"sheet {i}/{len(sheets)}")
            if s.records:
                for inv in s.records:
                    inv.update(page_start=i, page_end=i, source="spreadsheet")
                    invoices.append(inv)
            else:
                inv = extract_invoice(s.text, use_llm)
                if any(inv.get(k) is not None for k in FIELDS) and (inv.get("total") is not None or inv.get("invoice_number")):
                    inv.update(page_start=i, page_end=i, source="spreadsheet")
                    invoices.append(inv)
                else:
                    warnings.append(f"Sheet '{s.name}': no invoice data recognised")
    if not invoices:
        doc_issues.append(Issue("NO_INVOICE_FOUND", "error", "No invoice could be located in this document"))
    report(98, "validating")
    return ProcessedDocument(kind, pages, invoices, warnings, doc_issues)
