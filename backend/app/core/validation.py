"""Business-rule validation. Pure functions: no DB access (duplicates are passed in via `known`)."""
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

from .models import Issue
from .utils import CURRENCY_CODES, norm_invoice_no, norm_name, parse_date

REQUIRED = ("invoice_number", "invoice_date", "supplier_name", "total", "currency")
OPTIONAL = ("customer_name", "subtotal", "tax")
LOW_CONF = 0.6
# warnings that still force a human look
REVIEW_WARNINGS = {"LOW_CONFIDENCE", "UNGROUNDED_VALUE", "POSSIBLE_DUPLICATE", "IMPLIED_TAX", "SUSPICIOUS_TAX_RATE"}


def _tol(a: float, b: float) -> float:
    return max(0.02, 0.001 * max(abs(a), abs(b)))


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _tol(a, b)


def validate_invoice(inv: Dict, known: Optional[List[Dict]] = None, today: Optional[date] = None) -> List[Issue]:
    """`known`: other invoices (dicts with id, invoice_number, supplier_name, total, invoice_date) already stored."""
    today = today or date.today()
    issues: List[Issue] = []
    add = lambda code, sev, msg, f=None: issues.append(Issue(code, sev, msg, f))

    for f in REQUIRED:
        if inv.get(f) in (None, ""):
            add("MISSING_FIELD", "error", f"Required field '{f}' is missing", f)
    for f in OPTIONAL:
        if inv.get(f) in (None, ""):
            add("MISSING_OPTIONAL", "warning", f"Field '{f}' is missing", f)
    items = inv.get("line_items") or []
    if not items:
        add("NO_LINE_ITEMS", "warning", "No line items were extracted", "line_items")

    # numeric sanity
    for f in ("subtotal", "tax", "total"):
        v = inv.get(f)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
            add("INVALID_NUMBER", "error", f"'{f}' is not a valid number", f)
        elif v < 0:
            add("INVALID_NUMBER", "error", f"'{f}' is negative ({v}); credit notes need manual review", f)
    if isinstance(inv.get("total"), (int, float)) and inv["total"] == 0:
        add("ZERO_TOTAL", "warning", "Total is zero", "total")

    # date
    d = inv.get("invoice_date")
    if d:
        iso = parse_date(d)
        if not iso:
            add("INVALID_DATE", "error", f"Invoice date '{d}' is not a valid date", "invoice_date")
        else:
            dd = date.fromisoformat(iso)
            if dd > today + timedelta(days=1):
                add("FUTURE_DATE", "warning", f"Invoice date {iso} is in the future", "invoice_date")
            elif dd.year < 2000:
                add("INVALID_DATE", "error", f"Invoice date {iso} is implausibly old", "invoice_date")
    elif inv.get("invoice_date_raw"):
        add("INVALID_DATE", "error", f"Invoice date '{inv['invoice_date_raw']}' is not a valid date", "invoice_date")

    cur = inv.get("currency")
    if cur and cur not in CURRENCY_CODES:
        add("INVALID_CURRENCY", "error", f"Unknown currency code '{cur}'", "currency")

    # line-item arithmetic
    for i, it in enumerate(items, start=1):
        q, u, a = it.get("quantity"), it.get("unit_price"), it.get("amount")
        if None not in (q, u, a) and not _close(q * u, a):
            add("LINE_ITEM_MISMATCH", "error", f"Line {i}: {q} x {u} = {round(q * u, 2)} but amount is {a}", "line_items")

    sub, tax, tot = inv.get("subtotal"), inv.get("tax"), inv.get("total")
    amounts = [it["amount"] for it in items if it.get("amount") is not None]
    line_sum = round(sum(amounts), 2) if amounts and len(amounts) == len(items) else None
    if line_sum is not None and sub is not None and not _close(line_sum, sub):
        add("SUBTOTAL_MISMATCH", "error", f"Sum of line items ({line_sum}) differs from subtotal ({sub})", "subtotal")
    if sub is not None and tot is not None:
        if tax is not None:
            if not _close(sub + tax, tot):
                add("TOTAL_MISMATCH", "error", f"Subtotal ({sub}) + tax ({tax}) = {round(sub + tax, 2)} but total is {tot}", "total")
        elif not _close(sub, tot):
            add("IMPLIED_TAX", "warning", f"Total ({tot}) differs from subtotal ({sub}) but no tax was extracted", "tax")
    elif sub is None and line_sum is not None and tot is not None:
        expected = line_sum + (tax or 0.0)
        if not _close(expected, tot):
            add("TOTAL_MISMATCH", "error", f"Line items ({line_sum}) + tax ({tax or 0}) = {round(expected, 2)} but total is {tot}", "total")
    if sub and tax is not None and sub > 0 and tax / sub > 0.5:
        add("SUSPICIOUS_TAX_RATE", "warning", f"Tax is {tax / sub:.0%} of subtotal", "tax")

    # confidence / grounding
    for f, c in (inv.get("confidence") or {}).items():
        if c is not None and c < LOW_CONF and inv.get(f) is not None:
            add("LOW_CONFIDENCE", "warning", f"Low extraction confidence for '{f}' ({c:.2f})", f)
    for n in inv.get("notes") or []:
        add("UNGROUNDED_VALUE", "warning", n)

    # duplicates
    my_no, my_sup = norm_invoice_no(inv.get("invoice_number")), norm_name(inv.get("supplier_name"))
    for o in known or []:
        o_no, o_sup = norm_invoice_no(o.get("invoice_number")), norm_name(o.get("supplier_name"))
        same_sup = (my_sup == o_sup) if (my_sup and o_sup) else True
        if my_no and my_no == o_no and same_sup:
            add("DUPLICATE_INVOICE", "error", f"Same supplier and invoice number as invoice {o.get('id')}", "invoice_number")
        elif (same_sup and my_sup and tot is not None and o.get("total") is not None and _close(tot, o["total"])
              and inv.get("invoice_date") and inv.get("invoice_date") == o.get("invoice_date")):
            add("POSSIBLE_DUPLICATE", "warning", f"Same supplier, date and total as invoice {o.get('id')}", "invoice_number")
    return issues


def needs_review(issues: List[Issue]) -> bool:
    return any(i.severity == "error" or i.code in REVIEW_WARNINGS for i in issues)


def validate_batch(invoices: List[Dict]) -> List[List[Issue]]:
    """In-memory validation across a batch (used by evaluation). Earlier invoices count as 'known'."""
    results = []
    for n, inv in enumerate(invoices):
        known = [dict(id=f"#{j}", **{k: o.get(k) for k in ("invoice_number", "supplier_name", "total", "invoice_date")})
                 for j, o in enumerate(invoices[:n])]
        results.append(validate_invoice(inv, known))
    return results
