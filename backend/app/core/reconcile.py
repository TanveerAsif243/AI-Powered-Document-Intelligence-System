"""Cross-verification between two sets of invoices (PDF vs PDF, PDF vs Excel)."""
from typing import Dict, List

from .utils import norm_invoice_no, norm_name
from difflib import SequenceMatcher

COMPARE = ("invoice_date", "supplier_name", "subtotal", "tax", "total", "currency")


def _eq(f: str, a, b, tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if f in ("subtotal", "tax", "total"):
        return abs(a - b) <= tol
    if f == "supplier_name":
        na, nb = norm_name(a) or "", norm_name(b) or ""
        return na == nb or SequenceMatcher(None, na, nb).ratio() >= 0.9
    return a == b


def reconcile(left: List[Dict], right: List[Dict], tol: float = 0.01) -> Dict:
    idx: Dict[str, List[Dict]] = {}
    for r in right:
        k = norm_invoice_no(r.get("invoice_number"))
        if k:
            idx.setdefault(k, []).append(r)
    used, rows = set(), []
    for l in left:
        k = norm_invoice_no(l.get("invoice_number"))
        cands = idx.get(k, []) if k else []
        if not cands:
            rows.append({"status": "missing_in_right" if k else "no_invoice_number", "left_id": l.get("id"),
                         "invoice_number": l.get("invoice_number"), "differences": []})
            continue
        r = cands[0]
        used.add(r.get("id"))
        diffs = [{"field": f, "left": l.get(f), "right": r.get(f)} for f in COMPARE
                 if l.get(f) is not None and r.get(f) is not None and not _eq(f, l.get(f), r.get(f), tol)]
        rows.append({"status": "mismatch" if diffs else "match", "left_id": l.get("id"), "right_id": r.get("id"),
                     "invoice_number": l.get("invoice_number"), "differences": diffs})
    for r in right:
        if r.get("id") not in used:
            rows.append({"status": "missing_in_left", "right_id": r.get("id"),
                         "invoice_number": r.get("invoice_number"), "differences": []})
    summary: Dict[str, int] = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    return {"summary": summary, "rows": rows}
