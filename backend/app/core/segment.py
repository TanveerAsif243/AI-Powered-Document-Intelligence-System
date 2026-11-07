"""Split a multi-page document into invoice segments (a 1000-page PDF holds many invoices)."""
import re
from typing import List

from .models import PageResult, Segment
from .utils import norm_invoice_no

# Needs a digit in the captured token so "Invoice Date" is never read as an invoice number.
INV_NO_RE = re.compile(
    r"(?:invoice|inv|bill|receipt)\s*(?:no\.?|number|num|#|id)?\s*[:#.\-]?\s*"
    r"((?=[A-Za-z0-9\-/_.]*\d)[A-Za-z0-9][A-Za-z0-9\-/_.]{2,24})", re.I)
KEYWORDS = re.compile(r"invoice|receipt|amount due|grand total|total due|subtotal|tax", re.I)
HEADER_WORDS = re.compile(r"\b(tax\s+invoice|invoice|receipt)\b", re.I)
HAS_TOTAL = re.compile(r"(grand\s+total|total\s+due|amount\s+due|balance\s+due|(?<![A-Za-z])total)\s*[:\-]?\s*\S*\d", re.I)
MAX_SEGMENT_CHARS = 20000


def find_invoice_number(text: str):
    for m in INV_NO_RE.finditer(text):
        return m.group(1).rstrip(".-/_")
    return None


def segment_pages(pages: List[PageResult]) -> List[Segment]:
    segs: List[Segment] = []
    cur = None
    cur_no = None
    for p in pages:
        text = p.text or ""
        if not text.strip():
            if cur:
                cur.pages.append(p)
                cur.end = p.page_no
            continue
        no = find_invoice_number(text)
        key = norm_invoice_no(no)
        start_new = False
        if cur is None:
            start_new = bool(no) or bool(KEYWORDS.search(text))
            if not start_new:
                continue                      # leading non-invoice page (cover letter etc.)
        elif key and key != cur_no:
            start_new = True
        elif not key and HAS_TOTAL.search(cur.text) and HEADER_WORDS.search(text[:300]):
            start_new = True                  # next receipt without a number
        if start_new:
            cur = Segment(start=p.page_no, end=p.page_no, text=text, pages=[p])
            cur_no = key
            segs.append(cur)
        else:
            cur.text += "\n" + text
            cur.end = p.page_no
            cur.pages.append(p)
            if key and not cur_no:
                cur_no = key
        if cur and len(cur.text) > MAX_SEGMENT_CHARS:
            cur.text = cur.text[:MAX_SEGMENT_CHARS]
    return segs
