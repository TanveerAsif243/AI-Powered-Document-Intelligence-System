"""Extraction orchestrator: LLM (optional) + rules, normalisation, and grounding checks.

Grounding is the anti-hallucination layer: every value must be traceable to the source text,
otherwise it is discarded (set to null) and reported in `notes`.
"""
import re
from typing import Dict, List, Optional

from .extract_rules import AMT, DATE_PAT, extract_rules
from .llm import extract_with_llm, llm_available
from .models import FIELDS
from .utils import date_candidates, normalize_currency, parse_amount, parse_date, CURRENCY_SYMBOLS

AMOUNT_FIELDS = ("subtotal", "tax", "total")


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _numbers(text: str) -> List[float]:
    return [v for v in (parse_amount(t) for t in re.findall(AMT, text)) if v is not None]


def _grounded_string(value: str, text: str, nt: str, words: set) -> bool:
    nv = _alnum(value)
    if not nv:
        return False
    if nv in nt:
        return True
    toks = [w for w in re.findall(r"[a-z0-9]{3,}", value.lower())]
    return bool(toks) and sum(t in words for t in toks) / len(toks) >= 0.8


def _normalise(d: dict) -> dict:
    out = {k: d.get(k) for k in FIELDS}
    for k in AMOUNT_FIELDS:
        out[k] = parse_amount(out[k])
    if out["invoice_date"] is not None:
        out["invoice_date"] = parse_date(out["invoice_date"])
    out["currency"] = normalize_currency(out["currency"])
    for k in ("invoice_number", "supplier_name", "customer_name"):
        v = out[k]
        out[k] = str(v).strip() if v not in (None, "") else None
    items = []
    for it in d.get("line_items") or []:
        if isinstance(it, dict):
            items.append({"description": (str(it["description"]).strip() if it.get("description") else None),
                          "quantity": parse_amount(it.get("quantity")), "unit_price": parse_amount(it.get("unit_price")),
                          "amount": parse_amount(it.get("amount"))})
    out["line_items"] = items
    conf = {}
    for k, v in (d.get("confidence") or {}).items():
        try:
            conf[k] = max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            pass
    out["confidence"] = conf
    return out


def _ground(res: dict, text: str) -> dict:
    nt, words = _alnum(text), set(re.findall(r"[a-z0-9]{3,}", text.lower()))
    nums = _numbers(text)
    dates = set()
    for m in re.finditer(DATE_PAT, text):
        dates |= date_candidates(m.group(1))
    notes = res.setdefault("notes", [])

    def drop(k, why):
        res[k] = None
        res["confidence"].pop(k, None)
        notes.append(f"{k}: {why}; discarded")

    for k in ("invoice_number", "supplier_name", "customer_name"):
        if res[k] and not _grounded_string(res[k], text, nt, words):
            drop(k, "value not found in document text")
    for k in AMOUNT_FIELDS:
        if res[k] is not None and not any(abs(res[k] - n) < 0.006 for n in nums):
            drop(k, "amount not found in document text")
    if res["invoice_date"] and res["invoice_date"] not in dates:
        drop("invoice_date", "date not found in document text")
    if res["currency"]:
        sym_ok = any(c in text for c, code in CURRENCY_SYMBOLS.items() if code == res["currency"])
        if not (re.search(r"\b" + res["currency"] + r"\b", text) or sym_ok):
            drop("currency", "currency not printed in document text")
    kept = []
    for it in res["line_items"]:
        # description must be in the text; a bare number match is too easy to hit by coincidence (e.g. the "5" in "March 5")
        ok = _grounded_string(it["description"], text, nt, words) if it["description"] else \
             (it["amount"] is not None and any(abs(it["amount"] - n) < 0.006 for n in nums))
        if ok:
            kept.append(it)
        else:
            notes.append(f"line item '{it['description']}' not found in text; discarded")
    res["line_items"] = kept
    return res


def extract_invoice(text: str, use_llm: Optional[bool] = None) -> Dict:
    """Return the structured invoice dict. Missing/uncertain values stay None."""
    rules = _normalise(extract_rules(text))
    method = "rules"
    llm_raw = None
    if (llm_available() if use_llm is None else (use_llm and llm_available())):
        llm_raw = extract_with_llm(text)         # TransientError propagates => job retry
    if llm_raw:
        res = _normalise(llm_raw)
        method = "llm+rules"
        notes = []
        for k in FIELDS:                         # rules fill gaps; disagreements lower confidence
            if res[k] is None and rules[k] is not None and rules["confidence"].get(k, 0) >= 0.85:
                res[k], res["confidence"][k] = rules[k], rules["confidence"][k]
            elif res[k] is not None and rules[k] is not None and res[k] != rules[k] and rules["confidence"].get(k, 0) >= 0.85:
                res["confidence"][k] = min(res["confidence"].get(k, 0.5), 0.5)
                notes.append(f"{k}: LLM value '{res[k]}' disagrees with rule-based value '{rules[k]}'")
        if not res["line_items"]:
            res["line_items"] = rules["line_items"]
        for k in FIELDS:
            res["confidence"].setdefault(k, 0.7) if res[k] is not None else res["confidence"].pop(k, None)
        res["notes"] = notes
    else:
        res = rules
        res["notes"] = []
    res = _ground(res, text)
    res["method"] = method
    return res
